import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bus.trace import TRACE_FILENAME, JsonlTrace, TracingToolExecutor
from protocol.models import (
    AcceptanceRule,
    CriticResult,
    ExecutionContext,
    FailureCode,
    HITLDecision,
    HITLRequest,
    InputRef,
    RunConfig,
    RunResult,
    Task,
    TaskGraph,
    TaskResult,
    TaskStatus,
    ToolCall,
    ToolResult,
)


class FixedExecutor:
    def __init__(self, outcome: ToolResult | BaseException) -> None:
        self.outcome = outcome

    async def execute(self, call: ToolCall, context: ExecutionContext) -> ToolResult:
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def tool_call() -> tuple[ToolCall, ExecutionContext]:
    return (
        ToolCall(
            call_id="provider-call-id",
            name="file_read",
            arguments={"path": "C:\\private\\input.md", "content": "private arguments"},
        ),
        ExecutionContext(
            config=RunConfig(run_id="case-1"), task_id="read", agent_id="reader", attempt=1
        ),
    )


def read_trace(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


async def test_observer_trace_keeps_order_and_metadata(tmp_path):
    path = tmp_path / TRACE_FILENAME
    trace = JsonlTrace(path)
    rule = AcceptanceRule(type="file_exists", path="交接.md")
    writer = Task(
        task_id="write",
        assignee="writer",
        title="private task title",
        acceptance=[rule],
        outputs=["outputs/交接.md"],
    )
    inputs = [InputRef(from_task="write", path="交接.md", destination="inputs/交接.md")]
    reader = Task(
        task_id="read",
        assignee="reader",
        title="Read file",
        depends_on=["write"],
        acceptance=[rule],
        inputs=inputs,
        outputs=["outputs/answer.md"],
    )
    graph = TaskGraph(tasks=[writer, reader], final_task_id="read", goal_acceptance=[rule])
    start = datetime.now(UTC)
    finished = start + timedelta(seconds=2)
    result = TaskResult(
        task_id="read",
        agent_id="reader",
        status=TaskStatus.FAILED,
        started_at=start,
        finished_at=finished,
        failure_code=FailureCode.TOOL_ERROR,
        failure_reason="private failure text",
    )
    request = HITLRequest(
        request_id="private approval ID",
        task_id="read",
        kind="permission",
        action="private action",
        question="private question",
    )
    await trace.plan_ready("case-1", graph)
    await trace.tasks_delegated("case-1", writer, [reader])
    await trace.artifacts_handed_off("case-1", "read", inputs)
    await trace.task_changed("case-1", reader, result)
    await trace.hitl_decided(
        "case-1",
        request,
        HITLDecision(request_id=request.request_id, approved=False, source="policy"),
    )
    await trace.run_finished(
        RunResult(
            run_id="case-1",
            tasks=[result],
            started_at=start,
            finished_at=finished,
            failure_code=FailureCode.TOOL_ERROR,
            goal_verdict=CriticResult(passed=False, checks_passed=0, checks_total=1),
        )
    )
    records = read_trace(path)
    assert [record["event"] for record in records] == [
        "plan_ready",
        "tasks_delegated",
        "artifacts_handed_off",
        "task_changed",
        "hitl_decided",
        "run_finished",
    ]
    assert [record["sequence"] for record in records] == list(range(1, 7))
    assert {record["run_id"] for record in records} == {"case-1"}
    assert len({record["event_id"] for record in records}) == len(records)
    assert records[2]["inputs"][0]["destination"] == "inputs/交接.md"
    assert records[3]["failure_code"] == "tool_error"
    assert records[-1]["duration_sec"] == 2.0
    assert records[-1]["status"] == "failed"
    raw = path.read_text(encoding="utf-8")
    assert "交接.md" in raw and "private" not in raw


async def test_tool_trace_excludes_raw_arguments_results_and_error_paths(tmp_path):
    trace = JsonlTrace(tmp_path / TRACE_FILENAME)
    success = ToolResult(ok=True, data={"text": "private file content"})
    failure = ToolResult(ok=False, error="file not found: C:\\private\\output.md")
    call, context = tool_call()
    for outcome in (success, failure):
        executor = TracingToolExecutor(FixedExecutor(outcome), trace)
        assert await executor.execute(call, context) is outcome
    records = read_trace(trace.path)
    assert [record["ok"] for record in records] == [True, False]
    assert [record["failure_category"] for record in records] == [None, "missing_file"]
    assert {record["tool"] for record in records} == {"file_read"}
    assert {record["task_id"] for record in records} == {"read"}
    assert all(record["elapsed_sec"] >= 0 for record in records)
    raw = trace.path.read_text(encoding="utf-8")
    assert "private" not in raw and "provider-call-id" not in raw


@pytest.mark.parametrize(
    ("exception", "category"),
    [
        (RuntimeError("private error"), "tool_exception"),
        (asyncio.CancelledError("private cancellation"), "cancelled"),
    ],
)
async def test_trace_preserves_tool_exceptions(tmp_path, exception, category):
    trace = JsonlTrace(tmp_path / TRACE_FILENAME)
    executor = TracingToolExecutor(FixedExecutor(exception), trace)
    with pytest.raises(type(exception)) as caught:
        await executor.execute(*tool_call())
    assert caught.value is exception
    assert read_trace(trace.path)[0]["failure_category"] == category
    assert "private" not in trace.path.read_text(encoding="utf-8")


async def test_trace_write_failure_preserves_tool_result(tmp_path):
    trace = JsonlTrace(tmp_path)
    outcome = ToolResult(ok=True)
    executor = TracingToolExecutor(FixedExecutor(outcome), trace)
    for _ in range(10):
        assert await executor.execute(*tool_call()) is outcome
    assert len(trace.warnings) == 1
    assert trace.warnings[0].startswith("Trace write failed:")
    assert str(tmp_path) not in trace.warnings[0]
