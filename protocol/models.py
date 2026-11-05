"""Role A's shared contracts. Event serialization remains owned by Role D."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Literal, Self
from uuid import uuid4

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, JsonValue, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, validate_assignment=True)


Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")]


def relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or "\\" in value
        or ":" in value
        or "\x00" in value
        or str(path) != value
        or value == "."
    ):
        raise ValueError("expected a normalized relative POSIX file path")
    return value


RelativePath = Annotated[str, AfterValidator(relative_path)]


class FailureCode(StrEnum):
    TOOL_ERROR = "tool_error"
    TIMEOUT = "timeout"
    INVALID_OUTPUT = "invalid_output"
    MISSING_INPUT = "missing_input"
    HITL_BLOCKED = "hitl_blocked"
    MODEL_ERROR = "model_reasoning_error"
    DEPENDENCY_FAILED = "dependency_failed"
    ACCEPTANCE_FAILED = "acceptance_failed"
    BUDGET_EXCEEDED = "budget_exceeded"
    INTERNAL_ERROR = "internal_error"


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_CHILDREN = "waiting_children"
    WAITING_HUMAN = "waiting_human"
    REVIEWING = "reviewing"
    SUCCESS = "success"
    FAILED = "failed"
    BLOCKED = "blocked"


class AcceptanceRule(Contract):
    """The rule vocabulary and evaluator implementation are owned by D."""

    type: Literal["file_exists", "text_contains", "pytest_passes"]
    path: RelativePath
    text: str | None = None

    @model_validator(mode="after")
    def check_text(self) -> Self:
        if self.type == "text_contains" and not self.text:
            raise ValueError("text_contains requires non-empty text")
        if self.type != "text_contains" and self.text is not None:
            raise ValueError("text is only supported by text_contains")
        return self


class InputRef(Contract):
    from_task: Identifier
    path: RelativePath
    destination: RelativePath = Field(alias="as")

    @model_validator(mode="after")
    def in_inputs(self) -> Self:
        if not self.destination.startswith("inputs/"):
            raise ValueError("input destinations must be under inputs/")
        return self


class Task(Contract):
    task_id: Identifier
    title: str = Field(min_length=1, max_length=4000)
    assignee: Identifier
    parent_task: Identifier | None = None
    depth: int = Field(default=0, ge=0, le=1)
    depends_on: list[Identifier] = Field(default_factory=list)
    inputs: list[InputRef] = Field(default_factory=list)
    acceptance: list[AcceptanceRule] = Field(min_length=1)
    outputs: list[RelativePath] = Field(min_length=1)
    max_retries: int = Field(default=2, ge=0, le=10)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if bool(self.parent_task) != (self.depth == 1):
            raise ValueError("depth 1 requires a parent; depth 0 must not have one")
        if self.task_id in self.depends_on or len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError("dependencies must be unique and cannot contain the task itself")
        if any(not output.startswith("outputs/") for output in self.outputs):
            raise ValueError("task outputs must be files under outputs/")
        if len(set(self.outputs)) != len(self.outputs):
            raise ValueError("outputs must be unique")
        destinations = [ref.destination for ref in self.inputs]
        if len(set(destinations)) != len(destinations):
            raise ValueError("input destinations must be unique")
        if any(ref.from_task not in self.depends_on for ref in self.inputs):
            raise ValueError("every input source must be declared as a dependency")
        return self


class TaskGraph(Contract):
    tasks: list[Task] = Field(min_length=1, max_length=128)
    final_task_id: Identifier
    goal_acceptance: list[AcceptanceRule] = Field(min_length=1)
    summary: str = ""

    @model_validator(mode="after")
    def valid_graph(self) -> Self:
        by_id = {task.task_id: task for task in self.tasks}
        if len(by_id) != len(self.tasks):
            raise ValueError("task IDs must be unique")
        if self.final_task_id not in by_id:
            raise ValueError("final_task_id must identify an existing task")
        children: dict[str, int] = {}
        for task in self.tasks:
            if any(dep not in by_id for dep in task.depends_on):
                raise ValueError("dependency refers to an unknown task")
            if task.parent_task:
                parent = by_id.get(task.parent_task)
                if parent is None or parent.depth != 0:
                    raise ValueError("child tasks require an existing depth-0 parent")
                children[parent.task_id] = children.get(parent.task_id, 0) + 1
                if children[parent.task_id] > 2:
                    raise ValueError("each parent may delegate at most two subtasks")
        pending = {task.task_id: set(task.depends_on) for task in self.tasks}
        # A suspended parent also waits for its children: include those edges in cycle checks.
        for task in self.tasks:
            if task.parent_task:
                pending[task.parent_task].add(task.task_id)
        while pending:
            ready = {key for key, deps in pending.items() if not deps}
            if not ready:
                raise ValueError("task dependencies and delegation contain a cycle")
            pending = {key: deps - ready for key, deps in pending.items() if key not in ready}
        return self


class ToolCall(Contract):
    call_id: str = Field(min_length=1)
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class ToolResult(Contract):
    ok: bool
    data: JsonValue = None
    error: str | None = None
    elapsed: float = Field(default=0, ge=0)
    meta: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.ok and self.error is not None:
            raise ValueError("successful tool results must not contain an error")
        if not self.ok and not self.error:
            raise ValueError("failed tool results require an error")
        return self


class ToolDefinition(Contract):
    name: str
    description: str
    parameters: dict[str, JsonValue]
    requires_approval: bool = False


class AgentProfile(Contract):
    agent_id: Identifier
    role: Literal["planner", "worker", "critic"]
    system_prompt: str = Field(min_length=1)
    capabilities: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)


class RunConfig(Contract):
    run_id: Identifier = Field(default_factory=lambda: uuid4().hex)
    memory_enabled: bool = True
    write_memory: bool = True
    max_tasks: int = Field(default=32, ge=1, le=128)
    max_agent_steps: int = Field(default=12, ge=1, le=100)
    max_task_tokens: int = Field(default=16000, ge=1)
    max_output_tokens: int = Field(default=2048, ge=1)
    max_planning_calls: int = Field(default=8, ge=1, le=32)
    task_timeout_sec: float = Field(default=120, gt=0)
    approval_timeout_sec: float = Field(default=60, gt=0)


class HITLRequest(Contract):
    request_id: str = Field(default_factory=lambda: uuid4().hex)
    task_id: Identifier
    kind: Literal["clarification", "permission", "correction"]
    action: str = Field(min_length=1)
    question: str = Field(min_length=1)


class HITLDecision(Contract):
    request_id: str
    approved: bool
    answer: str = ""
    assumptions: list[str] = Field(default_factory=list)
    source: Literal["human", "policy", "timeout"]


class CriticResult(Contract):
    passed: bool
    checks_passed: int = Field(ge=0)
    checks_total: int = Field(ge=1)
    defects: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.checks_passed > self.checks_total:
            raise ValueError("passed checks cannot exceed the total")
        if self.passed != (self.checks_passed == self.checks_total):
            raise ValueError("passed must agree with the acceptance counts")
        return self


class TaskResult(Contract):
    task_id: Identifier
    agent_id: Identifier
    status: TaskStatus = TaskStatus.PENDING
    attempts: int = Field(default=0, ge=0)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    tokens: int = Field(default=0, ge=0)
    outputs: list[RelativePath] = Field(default_factory=list)
    summary: str = ""
    failure_code: FailureCode | None = None
    failure_reason: str | None = None
    critique: CriticResult | None = None
    hitl_decisions: list[HITLDecision] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RunResult(Contract):
    run_id: Identifier
    graph: TaskGraph | None = None
    tasks: list[TaskResult]
    started_at: datetime
    finished_at: datetime
    goal_verdict: CriticResult | None = None
    failure_code: FailureCode | None = None
    failure_reason: str | None = None
    warnings: list[str] = Field(default_factory=list)


class ExecutionContext(Contract):
    config: RunConfig
    task_id: Identifier
    agent_id: Identifier
    attempt: int = Field(ge=1)
    feedback: list[str] = Field(default_factory=list)
    child_results: list[TaskResult] = Field(default_factory=list)
    child_inputs: list[InputRef] = Field(default_factory=list)
    decision: HITLDecision | None = None


class AgentOutcome(Contract):
    kind: Literal["finished", "failed", "delegate", "ask_human"]
    summary: str = ""
    tokens: int = Field(default=0, ge=0)
    children: list[Task] = Field(default_factory=list, max_length=2)
    request: HITLRequest | None = None
    failure_code: FailureCode | None = None
    retryable: bool = True

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.kind == "delegate" and not self.children:
            raise ValueError("delegation requires children")
        if self.kind != "delegate" and self.children:
            raise ValueError("only delegation may contain children")
        if (self.kind == "ask_human") != (self.request is not None):
            raise ValueError("ask_human requires a request, other outcomes must not contain one")
        if self.kind == "failed" and self.failure_code is None:
            raise ValueError("failed outcomes require a failure code")
        return self


class ChatMessage(Contract):
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)


class ModelResponse(Contract):
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tokens: int = Field(default=0, ge=0)


class RoleBid(Contract):
    task_ids: list[Identifier]
    rationale: str = Field(min_length=1)


def extract_json(text: str) -> str:
    """Accept plain JSON or a single fenced JSON block, without guessing substrings."""
    match = re.fullmatch(r"\s*```(?:json)?\s*\n(.*?)\n```\s*", text, re.DOTALL)
    return match.group(1) if match else text.strip()
