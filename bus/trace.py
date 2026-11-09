"""Metadata-only JSONL tracing for scheduler events and tool calls."""

import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from protocol.models import (
    ExecutionContext,
    HITLDecision,
    HITLRequest,
    InputRef,
    RunResult,
    Task,
    TaskGraph,
    TaskResult,
    ToolCall,
    ToolResult,
)
from protocol.ports import ToolExecutor

TRACE_FILENAME = "trace.jsonl"


def _opaque_id(value: str) -> str:
    """Correlate provider IDs without recording their untrusted contents."""
    return hashlib.sha256(value.encode("utf-8", errors="surrogatepass")).hexdigest()


def _tool_failure_category(error: str) -> str:
    message = error.lower()
    prefixes = (
        ("unknown tool:", "unknown_tool"),
        ("missing required argument:", "invalid_argument"),
        ("invalid argument:", "invalid_argument"),
        ("file not found:", "missing_file"),
        ("expected a file,", "invalid_path"),
        ("permission denied:", "permission_denied"),
        ("path escapes workspace", "invalid_path"),
        ("execution exceeded", "timeout"),
    )
    for prefix, category in prefixes:
        if message.startswith(prefix):
            return category
    return "tool_error"


class JsonlTrace:
    """One ordered, best-effort metadata stream per case."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.warnings: list[str] = []
        self._sequence = 0

    def _append(self, event: str, run_id: str, **metadata: object) -> None:
        record = {
            "schema_version": "1.0",
            "event_id": uuid4().hex,
            "run_id": run_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "sequence": self._sequence + 1,
            "event": event,
            **metadata,
        }
        try:
            line = json.dumps(record, ensure_ascii=False, allow_nan=False)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(line + "\n")
        except (OSError, ValueError, TypeError) as exc:
            warning = f"Trace write failed: {type(exc).__name__}"
            if warning not in self.warnings and len(self.warnings) < 8:
                self.warnings.append(warning)
        else:
            self._sequence += 1

    async def plan_ready(self, run_id: str, graph: TaskGraph) -> None:
        self._append(
            "plan_ready",
            run_id,
            final_task_id=graph.final_task_id,
            tasks=[
                {"task_id": task.task_id, "agent_id": task.assignee, "depends_on": task.depends_on}
                for task in graph.tasks
            ],
        )

    async def tasks_delegated(self, run_id: str, parent: Task, children: list[Task]) -> None:
        self._append(
            "tasks_delegated",
            run_id,
            task_id=parent.task_id,
            agent_id=parent.assignee,
            child_task_ids=[child.task_id for child in children],
        )

    async def task_changed(self, run_id: str, task: Task, result: TaskResult) -> None:
        duration = None
        if result.started_at is not None and result.finished_at is not None:
            duration = (result.finished_at - result.started_at).total_seconds()
        self._append(
            "task_changed",
            run_id,
            task_id=task.task_id,
            agent_id=result.agent_id,
            status=result.status.value,
            attempt=result.attempts,
            tokens=result.tokens,
            duration_sec=duration,
            failure_code=result.failure_code.value if result.failure_code else None,
        )

    async def artifacts_handed_off(self, run_id: str, task_id: str, inputs: list[InputRef]) -> None:
        if inputs:
            self._append(
                "artifacts_handed_off",
                run_id,
                task_id=task_id,
                inputs=[
                    {
                        "from_task": item.from_task,
                        "path": item.path,
                        "destination": item.destination,
                    }
                    for item in inputs
                ],
            )

    async def hitl_decided(self, run_id: str, request: HITLRequest, decision: HITLDecision) -> None:
        self._append(
            "hitl_decided",
            run_id,
            task_id=request.task_id,
            request_id=_opaque_id(request.request_id),
            kind=request.kind,
            approved=decision.approved,
            source=decision.source,
        )

    async def run_finished(self, result: RunResult) -> None:
        passed = (
            result.failure_code is None
            and result.failure_reason is None
            and result.goal_verdict is not None
            and result.goal_verdict.passed
        )
        self._append(
            "run_finished",
            result.run_id,
            status="success" if passed else "failed",
            failure_code=result.failure_code.value if result.failure_code else None,
            duration_sec=(result.finished_at - result.started_at).total_seconds(),
        )

    def _tool_finished(
        self,
        call: ToolCall,
        context: ExecutionContext,
        *,
        ok: bool,
        elapsed_sec: float,
        failure_category: str | None,
    ) -> None:
        self._append(
            "tool_finished",
            context.config.run_id,
            task_id=context.task_id,
            agent_id=context.agent_id,
            attempt=context.attempt,
            tool=call.name,
            call_id=_opaque_id(call.call_id),
            ok=ok,
            elapsed_sec=elapsed_sec,
            failure_category=failure_category,
        )


class TracingToolExecutor:
    """Record tool call metadata while preserving its result and exceptions."""

    def __init__(self, executor: ToolExecutor, trace: JsonlTrace) -> None:
        self.executor = executor
        self.trace = trace

    async def execute(self, call: ToolCall, context: ExecutionContext) -> ToolResult:
        started = time.monotonic()
        try:
            result = await self.executor.execute(call, context)
        except (Exception, asyncio.CancelledError) as exc:
            category = "cancelled" if isinstance(exc, asyncio.CancelledError) else "tool_exception"
            self.trace._tool_finished(
                call,
                context,
                ok=False,
                elapsed_sec=time.monotonic() - started,
                failure_category=category,
            )
            raise
        self.trace._tool_finished(
            call,
            context,
            ok=result.ok,
            elapsed_sec=time.monotonic() - started,
            failure_category=_tool_failure_category(result.error or "") if not result.ok else None,
        )
        return result
