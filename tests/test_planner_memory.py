import json

import pytest

from bus.trace import JsonlTrace
from memory import JsonlMemory
from orchestrator.approvals import PolicyApprover
from orchestrator.bootstrap import build_service
from protocol.models import FailureCode, ModelResponse, ToolCall
from tests.helpers import (
    FixtureTools,
    ScriptedModel,
    StubVerifier,
    config,
    failed,
    graph,
    profile,
    task,
)
from tests.test_runtime import write_tool


def setup(root, replies, memory=None, verifier=None):
    planner_id = "custom-planner"
    memory = memory if memory is not None else JsonlMemory(root)
    model = ScriptedModel(*replies)
    trace = JsonlTrace(root / "trace.jsonl")
    service = build_service(
        root=root,
        model=model,
        profiles={
            planner_id: profile(planner_id, role="planner"),
            "writer": profile(tools=["file_write"]),
        },
        tool_definitions=[write_tool()],
        executor=FixtureTools(root),
        verifier=verifier or StubVerifier(),
        approver=PolicyApprover(),
        planner_id=planner_id,
        memory=memory,
        observer=trace,
        memory_observer=trace,
    )
    return service, model, memory, trace


def turn(task_id):
    return [
        graph(task(task_id)).model_dump_json(),
        ModelResponse(tool_calls=[
            ToolCall(
                call_id=f"write-{task_id}",
                name="file_write",
                arguments={"path": f"outputs/{task_id}.md", "text": "Report"},
            )
        ]),
        ModelResponse(content="Worker completed the report"),
    ]


@pytest.mark.parametrize("recall", [True, False])
async def test_planner_reuses_its_own_previous_goal_and_keeps_verdicts(tmp_path, recall):
    service, model, memory, trace = setup(tmp_path, [*turn("seed"), *turn("second")])
    seed = config(memory_enabled=True, write_memory=True, max_planning_calls=1)
    control = config(memory_enabled=recall, write_memory=False, max_planning_calls=1)
    first = await service.run_goal("核对发票总额，编写 DELTA-PLAN-171 状态报告", seed)
    second = await service.run_goal("重新核对发票总额，编写状态报告", control)
    assert first.goal_verdict.passed and second.goal_verdict.passed
    assert [node.task_id for node in second.tasks] == ["second"]
    calls = [json.loads(messages[1].content) for messages, _, _ in model.calls]
    planner_requests = [call for call in calls if "goal" in call]
    assert planner_requests[0]["memory_context"] == ""
    assert ("DELTA-PLAN-171" in planner_requests[1]["memory_context"]) is recall
    worker_requests = [call for call in calls if "task" in call]
    assert all("DELTA-PLAN-171" not in call["memory_context"] for call in worker_requests)

    record = json.loads(memory.path_for("custom-planner").read_text())
    trace_text = trace.path.read_text()
    assert "DELTA-PLAN-171" not in trace_text
    events = [
        event
        for line in trace_text.splitlines()
        if (event := json.loads(line)).get("agent_id") == "custom-planner"
    ]
    assert [evt["phase"] for evt in events if evt["run_id"] == seed.run_id] == [
        "retrieved", "injected", "reflected",
    ]
    second_events = [evt for evt in events if evt["run_id"] == control.run_id]
    assert [evt["phase"] for evt in second_events] == (["retrieved", "injected"] if recall else [])
    if recall:
        assert second_events[0]["memory_ids"] == [record["memory_id"]]
        assert second_events[0]["context_sha256"] == second_events[1]["context_sha256"]


@pytest.mark.parametrize("planning_fails", [True, False])
async def test_planner_reflects_distinct_failure_phases(tmp_path, planning_fails):
    replies = ["{}"] if planning_fails else turn("failed")
    service, _, memory, _ = setup(tmp_path, replies, verifier=StubVerifier(failed()))
    result = await service.run_goal(
        "Prepare status report", config(write_memory=True, max_planning_calls=1)
    )
    assert result.failure_code is not None
    record = json.loads(memory.path_for("custom-planner").read_text())
    assert record["outcome"] == "failed"
    assert (result.graph is None) is planning_fails
    expected = "before a valid task graph" if planning_fails else "downstream execution"
    assert expected in record["execution_summary"]


async def test_planner_adapter_warnings_do_not_replace_goal_failure(tmp_path):
    class BrokenMemory:
        async def retrieve(self, agent_id, task):
            raise OSError("private retrieval detail")

        async def reflect(self, task, result):
            raise OSError("private reflection detail")

    service, _, _, trace = setup(tmp_path, ["{}"], memory=BrokenMemory())
    result = await service.run_goal(
        "Prepare status report",
        config(memory_enabled=True, write_memory=True, max_planning_calls=1),
    )
    assert result.failure_code == FailureCode.MODEL_ERROR
    assert result.warnings == [
        "Planner memory retrieval failed: OSError", "Planner memory reflection failed: OSError",
    ]
    assert "private" not in trace.path.read_text()
