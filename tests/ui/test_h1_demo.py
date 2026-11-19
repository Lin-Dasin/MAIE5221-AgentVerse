"""Exercise a real scheduler pause/resume through the H1 demo entry point."""

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from harness.metrics import EvaluationReport
from protocol.models import FailureCode, HITLDecision, HITLRequest, TaskStatus
from ui.h1_demo import run_demo


def read_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


async def test_human_title_resumes_execution_and_persists_success(tmp_path: Path):
    asked = asyncio.Event()
    answered = asyncio.Event()
    requests: list[HITLRequest] = []
    title = "用户选的标题"

    async def answer(request: HITLRequest) -> HITLDecision:
        requests.append(request)
        asked.set()
        await answered.wait()
        return HITLDecision(
            request_id=request.request_id, approved=True, answer=title, source="human"
        )

    running = asyncio.create_task(run_demo(tmp_path, ask=answer, approval_timeout_sec=10))
    try:
        await asyncio.wait_for(asked.wait(), timeout=5)
        assert not running.done()
        trace_files = list(tmp_path.rglob("trace.jsonl"))
        assert len(trace_files) == 1
        paused = read_events(trace_files[0])
        assert any(
            event["event"] == "task_changed" and event["status"] == "waiting_human"
            for event in paused
        )
        assert not any(event["event"] == "hitl_decided" for event in paused)
        assert not list(tmp_path.rglob("*.md")), "No report may be written before the human answers"
        answered.set()
        report = await asyncio.wait_for(running, timeout=5)
    finally:
        if not running.done():
            running.cancel()
        await asyncio.gather(running, return_exceptions=True)

    assert len(requests) == 1
    assert requests[0].kind == "clarification"
    assert report.status == "success"
    assert report.failure_reason is None
    assert report.hitl_events == 1
    assert report.subtasks_passed == report.subtasks_total == 1
    assert report.acceptance_passed == report.acceptance_total
    assert report.raw_result.tasks[0].status == TaskStatus.SUCCESS

    run_dir = tmp_path / report.run_id
    evaluation = EvaluationReport.model_validate_json(
        (run_dir / "evaluation.json").read_text(encoding="utf-8")
    )
    assert evaluation.suite == "h1-terminal-clarification"
    assert len(evaluation.tasks) == 1
    assert evaluation.tasks[0].model_dump() == report.model_dump()
    assert evaluation.summary.goals_passed == evaluation.summary.goals_total == 1
    assert evaluation.summary.subtasks_passed == evaluation.summary.subtasks_total == 1
    assert evaluation.summary.goal_completion_rate == 1.0
    assert evaluation.summary.subtask_completion_rate == 1.0

    assert len(report.artifacts) == 1
    artifact = Path(report.artifacts[0])
    assert not artifact.is_absolute()
    artifact_path = (run_dir / artifact).resolve()
    assert artifact_path.is_relative_to(run_dir.resolve())
    assert artifact_path.read_text(encoding="utf-8").splitlines()[0] == f"# {title}"

    assert report.trace_path is not None and not Path(report.trace_path).is_absolute()
    trace_path = run_dir / report.trace_path
    events = read_events(trace_path)
    waiting = next(
        i
        for i, event in enumerate(events)
        if event["event"] == "task_changed" and event["status"] == "waiting_human"
    )
    decision = next(i for i, event in enumerate(events) if event["event"] == "hitl_decided")
    resumed = next(
        i
        for i, event in enumerate(events)
        if i > decision and event["event"] == "task_changed" and event["status"] == "running"
    )
    successful = next(
        i
        for i, event in enumerate(events)
        if event["event"] == "task_changed" and event["status"] == "success"
    )
    assert waiting < decision < resumed < successful < len(events) - 1
    assert events[-1]["event"] == "run_finished" and events[-1]["status"] == "success"
    assert events[decision]["source"] == "human" and events[decision]["approved"]
    assert (
        events[decision]["request_id"]
        == hashlib.sha256(requests[0].request_id.encode("utf-8")).hexdigest()
    )
    raw_trace = trace_path.read_text(encoding="utf-8")
    assert title not in raw_trace
    assert requests[0].request_id not in raw_trace


@pytest.mark.parametrize("response", ["decline", "timeout"])
async def test_unapproved_request_finishes_without_writing_report(tmp_path: Path, response: str):
    requests: list[HITLRequest] = []

    async def answer(request: HITLRequest) -> HITLDecision:
        requests.append(request)
        if response == "timeout":
            await asyncio.Event().wait()
        return HITLDecision(
            request_id=request.request_id,
            approved=False,
            answer="Private refusal: do not write a report",
            source="human",
        )

    report = await asyncio.wait_for(
        run_demo(tmp_path, ask=answer, approval_timeout_sec=0.05), timeout=5
    )
    assert len(requests) == 1
    assert report.status == "failed"
    assert report.failure_reason
    assert report.artifacts == []
    assert not list(tmp_path.rglob("*.md"))
    assert report.hitl_events == 1
    assert report.subtasks_total == 1 and report.subtasks_passed == 0
    assert report.raw_result.tasks[0].failure_code == FailureCode.HITL_BLOCKED
    decision = report.raw_result.tasks[0].hitl_decisions[0]
    assert not decision.approved
    assert decision.source == ("timeout" if response == "timeout" else "human")

    run_dir = tmp_path / report.run_id
    evaluation = EvaluationReport.model_validate_json(
        (run_dir / "evaluation.json").read_text(encoding="utf-8")
    )
    assert evaluation.summary.goals_total == 1 and evaluation.summary.goals_passed == 0
    assert evaluation.summary.goal_completion_rate == 0.0
    assert evaluation.summary.subtask_completion_rate == 0.0
    assert evaluation.tasks[0].status == "failed"
    assert report.trace_path is not None
    events = read_events(run_dir / report.trace_path)
    assert any(
        event["event"] == "task_changed" and event["status"] == "waiting_human" for event in events
    )
    decisions = [event for event in events if event["event"] == "hitl_decided"]
    assert len(decisions) == 1
    assert decisions[0]["source"] == decision.source and not decisions[0]["approved"]
    assert not any(
        event["event"] == "tool_finished" and event["tool"] == "write_demo_report"
        for event in events
    )
    assert events[-1]["event"] == "run_finished" and events[-1]["status"] == "failed"
    assert "Private refusal" not in (run_dir / report.trace_path).read_text(encoding="utf-8")
