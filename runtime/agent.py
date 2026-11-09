"""A resumable agent loop. Actual tool execution is delegated to Role B."""

import json
from dataclasses import dataclass, field
from typing import Literal

from pydantic import Field, ValidationError

from protocol.models import (
    AgentOutcome,
    AgentProfile,
    ChatMessage,
    Contract,
    ExecutionContext,
    FailureCode,
    HITLRequest,
    Task,
    ToolCall,
    ToolDefinition,
    ToolResult,
)
from protocol.ports import Memory, ModelClient, ToolExecutor
from runtime.client import ModelCallError


class DelegateArguments(Contract):
    tasks: list[Task] = Field(min_length=1, max_length=2)


class QuestionArguments(Contract):
    kind: Literal["clarification", "permission", "correction"]
    action: str = Field(min_length=1)
    question: str = Field(min_length=1)


INTERNAL_TOOLS = [
    ToolDefinition(
        name="delegate",
        description="Request up to two specialist subtasks through the scheduler.",
        parameters=DelegateArguments.model_json_schema(),
    ),
    ToolDefinition(
        name="ask_human",
        description="Pause for clarification, permission or correction.",
        parameters=QuestionArguments.model_json_schema(),
    ),
]


@dataclass
class _Session:
    messages: list[ChatMessage]
    steps: int = 0
    tokens: int = 0
    pending: ToolCall | None = None
    request: HITLRequest | None = None
    queue: list[ToolCall] = field(default_factory=list)


class AgentRuntime:
    def __init__(
        self,
        model: ModelClient,
        executor: ToolExecutor,
        profiles: dict[str, AgentProfile],
        tools: list[ToolDefinition],
        *,
        memory: Memory | None = None,
    ):
        self.model = model
        self.executor = executor
        self.profiles = profiles
        self.tools = {tool.name: tool for tool in tools}
        if len(self.tools) != len(tools) or self.tools.keys() & {"delegate", "ask_human"}:
            raise ValueError("tool names must be unique and must not use reserved control names")
        for key, profile in profiles.items():
            if key != profile.agent_id or not set(profile.tools) <= self.tools.keys():
                raise ValueError("profile identifiers and tool names must match the registry")
        self.memory = memory
        self.sessions: dict[tuple[str, str, int], _Session] = {}

    def release(self, run_id: str, task_id: str) -> None:
        for key in list(self.sessions):
            if key[:2] == (run_id, task_id):
                del self.sessions[key]

    @staticmethod
    def _tool_message(session: _Session, call: ToolCall, result: ToolResult) -> None:
        session.messages.append(
            ChatMessage(role="tool", tool_call_id=call.call_id, content=result.model_dump_json())
        )

    async def _call(self, call: ToolCall, context: ExecutionContext) -> ToolResult:
        try:
            return await self.executor.execute(call, context)
        except Exception as exc:
            return ToolResult(ok=False, error=f"Tool execution failed: {type(exc).__name__}")

    async def execute(self, task: Task, context: ExecutionContext) -> AgentOutcome:
        profile = self.profiles.get(task.assignee)
        if profile is None or profile.role != "worker":
            return AgentOutcome(
                kind="failed",
                failure_code=FailureCode.INVALID_OUTPUT,
                summary="Task assignee is not a registered worker",
                retryable=False,
            )
        if context.task_id != task.task_id or context.agent_id != task.assignee:
            raise ValueError("execution context does not match the task")
        key = (context.config.run_id, task.task_id, context.attempt)
        session = self.sessions.get(key)
        if session is None:
            memory_context = ""
            if context.config.memory_enabled:
                if self.memory is None:
                    raise ValueError("memory_enabled requires a Role C memory adapter")
                memory_context = await self.memory.retrieve(task.assignee, task)
            session = _Session(
                [
                    ChatMessage(
                        role="system",
                        content=profile.system_prompt + "\n"
                        "Work inside your assigned workspace using only the provided tools. "
                        "Use delegate for specialist work and ask_human for ambiguity. "
                        "Finish with a concise summary after producing the declared output files.",
                    ),
                    ChatMessage(
                        role="user",
                        content=json.dumps(
                            {
                                "task": task.model_dump(mode="json", by_alias=True),
                                "memory_context": memory_context,
                                "feedback": context.feedback,
                                "child_results": [
                                    item.model_dump(mode="json") for item in context.child_results
                                ],
                                "child_inputs": [
                                    item.model_dump(by_alias=True) for item in context.child_inputs
                                ],
                            }
                        ),
                    ),
                ]
            )
            self.sessions[key] = session
        try:
            if session.pending is not None:
                call = session.pending
                if call.name == "delegate":
                    self._tool_message(
                        session,
                        call,
                        ToolResult(
                            ok=True,
                            data={
                                "results": [
                                    item.model_dump(mode="json") for item in context.child_results
                                ],
                                "inputs": [
                                    item.model_dump(by_alias=True) for item in context.child_inputs
                                ],
                            },
                        ),
                    )
                else:
                    decision = context.decision
                    if decision is None or session.request is None:
                        raise ValueError("resuming approval requires its decision")
                    if decision.request_id != session.request.request_id:
                        raise ValueError("decision belongs to another request")
                    if not decision.approved:
                        return AgentOutcome(
                            kind="failed",
                            summary=decision.answer,
                            tokens=session.tokens,
                            failure_code=FailureCode.HITL_BLOCKED,
                            retryable=False,
                        )
                    result = (
                        ToolResult(ok=True, data=decision.model_dump(mode="json"))
                        if call.name == "ask_human"
                        else await self._call(call, context)
                    )
                    self._tool_message(session, call, result)
                session.pending = None
                session.request = None
            available = [self.tools[name] for name in profile.tools] + INTERNAL_TOOLS
            while True:
                if session.queue:
                    call = session.queue.pop(0)
                    if call.name == "delegate":
                        arguments = DelegateArguments.model_validate(call.arguments)
                        if task.depth or any(
                            child.assignee not in self.profiles for child in arguments.tasks
                        ):
                            raise ValueError("invalid delegation depth or assignee")
                        session.pending = call
                        return AgentOutcome(
                            kind="delegate",
                            children=arguments.tasks,
                            tokens=session.tokens,
                        )
                    if call.name == "ask_human":
                        args = QuestionArguments.model_validate(call.arguments)
                        request = HITLRequest(task_id=task.task_id, **args.model_dump())
                        session.pending, session.request = call, request
                        return AgentOutcome(
                            kind="ask_human", request=request, tokens=session.tokens
                        )
                    if call.name not in profile.tools:
                        self._tool_message(
                            session,
                            call,
                            ToolResult(
                                ok=False,
                                error="Tool is not declared in this AgentProfile",
                            ),
                        )
                        continue
                    definition = self.tools[call.name]
                    if definition.requires_approval:
                        request = HITLRequest(
                            task_id=task.task_id,
                            kind="permission",
                            action=f"tool:{call.name}",
                            question=f"Approve {call.name} for task {task.task_id}? "
                            f"Arguments: {json.dumps(call.arguments)}",
                        )
                        session.pending, session.request = call, request
                        return AgentOutcome(
                            kind="ask_human", request=request, tokens=session.tokens
                        )
                    self._tool_message(session, call, await self._call(call, context))
                    continue
                remaining = context.config.max_task_tokens - session.tokens
                if session.steps >= context.config.max_agent_steps or remaining <= 0:
                    return AgentOutcome(
                        kind="failed",
                        tokens=session.tokens,
                        retryable=False,
                        failure_code=FailureCode.BUDGET_EXCEEDED,
                        summary="Agent exceeded its step or token budget",
                    )
                response = await self.model.complete(
                    session.messages,
                    available,
                    max_tokens=min(context.config.max_output_tokens, remaining),
                )
                session.steps += 1
                session.tokens += response.tokens
                if session.tokens > context.config.max_task_tokens:
                    return AgentOutcome(
                        kind="failed",
                        tokens=session.tokens,
                        retryable=False,
                        failure_code=FailureCode.BUDGET_EXCEEDED,
                        summary="Agent exceeded its token budget",
                    )
                if len(response.tool_calls) > 16:
                    raise ValueError("too many tool calls in one model response")
                session.messages.append(
                    ChatMessage(
                        role="assistant",
                        content=response.content,
                        tool_calls=response.tool_calls,
                    )
                )
                if not response.tool_calls:
                    return AgentOutcome(
                        kind="finished",
                        summary=response.content,
                        tokens=session.tokens,
                    )
                session.queue.extend(response.tool_calls)
        except (ValueError, ValidationError) as exc:
            return AgentOutcome(
                kind="failed",
                tokens=session.tokens,
                failure_code=FailureCode.INVALID_OUTPUT,
                summary=str(exc),
            )
        except ModelCallError as exc:
            return AgentOutcome(
                kind="failed",
                tokens=session.tokens,
                failure_code=FailureCode.MODEL_ERROR,
                summary=str(exc),
            )
