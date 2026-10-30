"""Typed integration ports; B/C/D implement adapters in their own modules.

These are call contracts, not implementations of tools, memory, verdicts or events.
In particular, D maps Observer callbacks to its own bus/events.py schema.
"""

from pathlib import Path
from typing import Protocol

from protocol.models import (
    AcceptanceRule,
    AgentOutcome,
    ChatMessage,
    CriticResult,
    ExecutionContext,
    HITLDecision,
    HITLRequest,
    InputRef,
    ModelResponse,
    Task,
    TaskGraph,
    TaskResult,
    ToolCall,
    ToolDefinition,
    ToolResult,
)


class ModelClient(Protocol):
    async def complete(
        self, messages: list[ChatMessage], tools: list[ToolDefinition], *, max_tokens: int
    ) -> ModelResponse: ...


class ToolExecutor(Protocol):
    """B dispatches calls using context.agent_id and the shared workspace root.

    Implementations validate arguments, enforce execution limits and return the
    common ToolResult. D may wrap this interface to persist per-call traces.
    """

    async def execute(self, call: ToolCall, context: ExecutionContext) -> ToolResult: ...


class Memory(Protocol):
    """C's public async facade; retrieval returns prompt-ready text, not records.

    Empty history returns "". Reflection uses task.assignee/result.agent_id;
    result.critique is optional (for example when execution never reached review).
    Internal record retrieval, ranking and formatting remain C's implementation.
    """

    async def retrieve(self, agent_id: str, task: Task) -> str: ...

    async def reflect(self, task: Task, result: TaskResult) -> None: ...


class Verifier(Protocol):
    """D's deterministic acceptance component, not an implemented Critic agent.

    Task roots are worker workspaces; goal roots are the final task archive.
    Code-based checks must use B's isolated executor rather than execute on host.
    """

    async def check(self, rules: list[AcceptanceRule], root: Path) -> CriticResult: ...


class Approver(Protocol):
    async def decide(self, request: HITLRequest) -> HITLDecision: ...


class Worker(Protocol):
    async def execute(self, task: Task, context: ExecutionContext) -> AgentOutcome: ...

    def release(self, run_id: str, task_id: str) -> None: ...


class Observer(Protocol):
    """D maps these task callbacks to its event schema and routing rules.

    This interface neither receives chat messages nor records individual model
    and tool calls. Those need D's routing and client/executor instrumentation.
    """

    async def plan_ready(self, run_id: str, graph: TaskGraph) -> None: ...

    async def tasks_delegated(self, run_id: str, parent: Task, children: list[Task]) -> None: ...

    async def task_changed(self, run_id: str, task: Task, result: TaskResult) -> None: ...

    async def artifacts_handed_off(
        self, run_id: str, task_id: str, inputs: list[InputRef]
    ) -> None: ...

    async def hitl_decided(
        self, run_id: str, request: HITLRequest, decision: HITLDecision
    ) -> None: ...
