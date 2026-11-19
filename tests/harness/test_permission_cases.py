"""Permission scenarios run through real A scheduling, even without B installed."""

import asyncio
import json
from pathlib import Path

import pytest

from harness.metrics import EvaluationReport
from harness.permission_cases import (
    MARKER,
    MARKER_CONTENT,
    TerminalPermission,
    run_permission_case,
)
from protocol.models import FailureCode, HITLDecision, HITLRequest, ToolDefinition, ToolResult


@pytest.fixture
def backend():
    calls = []

    def factory(root):
        class FixtureExecutor:
            async def execute(self, call, context):
                calls.append(call)
                workspace = root / "workspaces" / context.agent_id
                path = workspace / call.arguments["path"]
                if call.name == "file_delete":
                    path.unlink()
                    return ToolResult(ok=True, data={"deleted": True})
                path.write_text(call.arguments["text"], encoding="utf-8")
                return ToolResult(ok=True, data={"path": call.arguments["path"]})

        definitions = [
            ToolDefinition(
                name=name,
                description=name,
                parameters={"type": "object"},
                requires_approval=name == "file_delete",
            )
            for name in ["file_delete", "file_write"]
        ]
        return FixtureExecutor(), definitions

    return factory, calls


async def approve(request):
    return HITLDecision(request_id=request.request_id, approved=True, answer="yes", source="human")


def read_evaluation(result):
    return EvaluationReport.model_validate_json(
        (result.run_dir / "evaluation.json").read_text(encoding="utf-8")
    )


async def test_h2_waits_for_human_before_real_runtime_dispatch(tmp_path: Path, backend):
    factory, calls = backend
    requested, answered = asyncio.Event(), asyncio.Event()
    requests = []

    async def answer(request):
        requests.append(request)
        requested.set()
        await answered.wait()
        return await approve(request)

    running = asyncio.create_task(
        run_permission_case(
            tmp_path, scenario="h2", ask=answer, executor_factory=factory, approval_timeout_sec=10
        )
    )
    try:
        await asyncio.wait_for(requested.wait(), timeout=5)
        assert not running.done()
        markers = list(tmp_path.rglob(MARKER))
        assert len(markers) == 1 and markers[0].read_text(encoding="utf-8") == MARKER_CONTENT
        assert calls == []
        answered.set()
        result = await asyncio.wait_for(running, timeout=5)
    finally:
        if not running.done():
            running.cancel()
        await asyncio.gather(running, return_exceptions=True)

    assert requests[0].kind == "permission" and requests[0].action == "tool:file_delete"
    assert result.control_passed and all(result.checks.values())
    assert result.backend == "injected"
    assert result.delete_calls == result.successful_delete_calls == 1
    assert result.approval_before_delete and not result.marker_exists
    assert [call.name for call in calls] == ["file_delete", "file_write"]
    assert result.report.status == "success"
    evaluation = read_evaluation(result)
    assert evaluation.tasks[0] == result.report
    assert (
        evaluation.summary.goal_completion_rate == evaluation.summary.subtask_completion_rate == 1
    )
    events = [
        json.loads(line) for line in (result.run_dir / "trace.jsonl").read_text().splitlines()
    ]
    decision = next(event for event in events if event["event"] == "hitl_decided")
    deletion = next(event for event in events if event.get("tool") == "file_delete")
    assert decision["approved"] and deletion["ok"] and decision["sequence"] < deletion["sequence"]
    control = json.loads((result.run_dir / "control.json").read_text(encoding="utf-8"))
    assert control["control_passed"] and control["backend"] == "injected"


async def test_h3_expected_denial_passes_control_but_preserves_raw_failure(tmp_path: Path, backend):
    factory, calls = backend

    async def must_not_prompt(request):
        pytest.fail("Policy denial must not prompt a human")

    result = await run_permission_case(
        tmp_path, scenario="h3", ask=must_not_prompt, executor_factory=factory
    )
    assert result.control_passed and result.marker_unchanged and result.marker_exists
    assert result.delete_calls == result.successful_delete_calls == 0
    assert calls == []
    assert result.report.status == "failed" and result.report.subtasks_passed == 0
    assert result.report.subtasks_total == 1
    task = result.report.raw_result.tasks[0]
    assert task.failure_code == FailureCode.HITL_BLOCKED
    assert len(task.hitl_decisions) == 1
    assert task.hitl_decisions[0].source == "policy" and not task.hitl_decisions[0].approved
    evaluation = read_evaluation(result)
    assert (
        evaluation.summary.goal_completion_rate == evaluation.summary.subtask_completion_rate == 0
    )
    assert not result.report.artifacts


@pytest.mark.parametrize("response", ["deny", "timeout"])
async def test_h2_without_approval_never_deletes(tmp_path: Path, backend, response):
    factory, calls = backend

    async def answer(request):
        if response == "timeout":
            await asyncio.Event().wait()
        return HITLDecision(request_id=request.request_id, approved=False, source="human")

    result = await asyncio.wait_for(
        run_permission_case(
            tmp_path, scenario="h2", ask=answer, executor_factory=factory, approval_timeout_sec=0.05
        ),
        timeout=5,
    )
    assert not result.control_passed
    assert result.marker_unchanged and result.delete_calls == 0 and calls == []
    assert result.report.raw_result.tasks[0].hitl_decisions[0].source == (
        "timeout" if response == "timeout" else "human"
    )


async def test_repeated_runs_keep_separate_markers_and_evidence(tmp_path: Path, backend):
    factory, _ = backend
    first = await run_permission_case(tmp_path, scenario="h3", executor_factory=factory)
    original = (first.run_dir / "evaluation.json").read_bytes()
    second = await run_permission_case(
        tmp_path, scenario="h2", ask=approve, executor_factory=factory
    )
    assert first.run_dir != second.run_dir
    assert first.run_dir.parent == second.run_dir.parent == tmp_path.resolve()
    assert (first.run_dir / "workspaces" / "writer" / MARKER).read_text() == MARKER_CONTENT
    assert (first.run_dir / "evaluation.json").read_bytes() == original
    assert first.control_passed and second.control_passed
    assert first.report.raw_result.graph == second.report.raw_result.graph


async def test_successful_goal_cannot_hide_a_marker_that_was_not_deleted(tmp_path: Path, backend):
    factory, _ = backend

    def misleading_factory(root):
        executor, definitions = factory(root)

        class MisleadingExecutor:
            async def execute(self, call, context):
                if call.name == "file_delete":
                    return ToolResult(ok=True, data={"deleted": True})
                return await executor.execute(call, context)

        return MisleadingExecutor(), definitions

    result = await run_permission_case(
        tmp_path, scenario="h2", ask=approve, executor_factory=misleading_factory
    )
    assert result.report.status == "success" and result.successful_delete_calls == 1
    assert result.marker_unchanged
    assert not result.control_passed and not result.checks["marker_deleted"]


@pytest.mark.parametrize(
    "text, expected", [("yes", True), (" Y ", True), ("no", False), ("", False)]
)
async def test_terminal_permission_requires_explicit_yes(text, expected):
    request = HITLRequest(
        task_id="demo", kind="permission", action="tool:file_delete", question="Delete?"
    )
    decision = await TerminalPermission(lambda prompt: text)(request)
    assert decision.request_id == request.request_id
    assert decision.source == "human" and decision.approved is expected
    assert request.kind == "permission"


@pytest.mark.parametrize("scenario", ["h2", "h3"])
async def test_real_role_b_file_delete_when_available(tmp_path: Path, scenario):
    pytest.importorskip("tools.fs", reason="Role B integration checkout is optional on Role_D")
    pytest.importorskip("sandbox.executor")
    result = await run_permission_case(tmp_path, scenario=scenario, ask=approve)
    assert result.backend == "role_b" and result.control_passed
    assert result.marker_exists is (scenario == "h3")
    assert result.delete_calls == (1 if scenario == "h2" else 0)
