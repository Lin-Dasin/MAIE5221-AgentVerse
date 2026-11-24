import asyncio

import pytest

from harness.metrics import summarize_result
from orchestrator.approvals import PolicyApprover
from orchestrator.bootstrap import build_service
from protocol.models import FailureCode
from tests.helpers import FixtureTools, ScriptedModel, StubVerifier, config, graph, profile, task


def make_service(root, planned):
    return build_service(
        root=root,
        model=ScriptedModel(planned.model_dump_json()),
        profiles={"planner": profile("planner", role="planner"), "writer": profile()},
        tool_definitions=[],
        executor=FixtureTools(root),
        verifier=StubVerifier(),
        approver=PolicyApprover(),
    )


@pytest.mark.parametrize("mode", ["goal", "graph"])
@pytest.mark.parametrize(
    ("error", "code"),
    [(TimeoutError, FailureCode.TIMEOUT), (RuntimeError, FailureCode.INTERNAL_ERROR)],
)
async def test_execution_error_is_structured_and_reported(tmp_path, monkeypatch, mode, error, code):
    planned = graph(task())
    cfg = config(max_planning_calls=1)
    service = make_service(tmp_path, planned)

    async def fail(_graph, _config):
        raise error("private adapter data")

    monkeypatch.setattr(service.scheduler, "run", fail)
    if mode == "goal":
        result = await service.run_goal("Write a report", cfg)
    else:
        result = await service.run_graph(planned, cfg)
    assert result.failure_code == code
    assert result.graph == planned
    assert result.tasks == [] and result.goal_verdict is None
    assert result.run_id == cfg.run_id
    assert "private adapter data" not in result.failure_reason
    report = summarize_result("case", 1, result, cfg, tmp_path)
    assert report.status == "failed"
    assert report.subtasks_total == 0 and report.unreported_task_ids == ["t1"]


async def test_service_does_not_turn_cancellation_into_a_verdict(tmp_path, monkeypatch):
    planned = graph(task())
    service = make_service(tmp_path, planned)

    async def cancel(_graph, _config):
        raise asyncio.CancelledError

    monkeypatch.setattr(service.scheduler, "run", cancel)
    with pytest.raises(asyncio.CancelledError):
        await service.run_graph(planned, config())
