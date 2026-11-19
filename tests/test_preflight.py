"""Plan contracts fail or repair before workers and artifact stores are touched."""

import json

import pytest

from orchestrator.approvals import PolicyApprover
from orchestrator.bootstrap import build_service
from orchestrator.planner import Planner, PlanningError
from orchestrator.preflight import PlanContractError, PlanPreflight
from orchestrator.scheduler import Scheduler
from protocol.models import AcceptanceRule, AgentOutcome, FailureCode, InputRef, ModelResponse
from tests.helpers import (
    FixtureTools,
    ScriptedModel,
    ScriptedWorker,
    StubVerifier,
    config,
    graph,
    profile,
    task,
)


def linked_graph():
    return graph(
        task("first"),
        task(
            "last",
            depends_on=["first"],
            inputs=[
                InputRef(from_task="first", path="first.md", destination="inputs/brief.md"),
            ],
        ),
    )


def break_contract(planned, defect):
    if defect == "input":
        planned.tasks[-1].inputs[0].path = "outputs/first.md"
    elif defect == "goal":
        planned.goal_acceptance[0].path = "outputs/last.md"
    elif defect == "rule":
        planned.tasks[0].acceptance = [AcceptanceRule(type="pytest_passes", path="tests")]


@pytest.mark.parametrize(
    "defect,diagnostic",
    [
        ("input", "not published by first"),
        ("goal", "not published by final task last"),
        ("rule", "unsupported acceptance types"),
    ],
)
async def test_planner_repairs_contract_errors_and_preserves_reasoning(defect, diagnostic):
    valid = linked_graph()
    invalid = valid.model_copy(deep=True)
    break_contract(invalid, defect)
    model = ScriptedModel(
        ModelResponse(content=invalid.model_dump_json(), reasoning_content="synthetic reasoning"),
        valid.model_dump_json(),
    )
    result = await Planner(model, profile("planner", role="planner")).plan(
        "Write a report",
        [profile()],
        config(),
        negotiate=False,
    )
    assert result == valid
    repair_messages = model.calls[1][0]
    assert diagnostic in repair_messages[-1].content
    assert repair_messages[-2].reasoning_content == "synthetic reasoning"
    assert json.loads(model.calls[0][0][1].content)["supported_acceptance_types"] == [
        "file_exists",
        "text_contains",
    ]


@pytest.mark.parametrize("defect", ["input", "goal", "rule"])
async def test_fixed_graph_rejected_before_workspace_or_worker_side_effects(tmp_path, defect):
    planned = linked_graph()
    break_contract(planned, defect)
    worker = ScriptedWorker(tmp_path)
    verifier = StubVerifier()
    scheduler = Scheduler(worker, verifier, PolicyApprover(), root=tmp_path)
    with pytest.raises(PlanContractError):
        await scheduler.run(planned, config())
    assert worker.calls == [] and verifier.calls == []
    assert list(tmp_path.iterdir()) == []


async def test_negotiated_plan_is_rechecked_and_dataset_rules_remain_authoritative():
    valid = linked_graph()
    invalid = valid.model_copy(deep=True)
    break_contract(invalid, "input")
    required = [AcceptanceRule(type="text_contains", path="last.md", text="Conclusion")]
    model = ScriptedModel(
        valid.model_dump_json(),
        '{"task_ids":["last"],"rationale":"Can write"}',
        invalid.model_dump_json(),
        valid.model_dump_json(),
    )
    result = await Planner(model, profile("planner", role="planner")).plan(
        "Write a report",
        [profile()],
        config(),
        goal_acceptance=required,
    )
    assert len(model.calls) == 4
    assert "not published by first" in model.calls[-1][0][-1].content
    assert result.goal_acceptance == required


async def test_contract_repair_exhaustion_retains_actionable_diagnostic():
    invalid = linked_graph()
    break_contract(invalid, "goal")
    model = ScriptedModel(invalid.model_dump_json(), invalid.model_dump_json())
    with pytest.raises(PlanningError, match="not published by final task"):
        await Planner(model, profile("planner", role="planner")).plan(
            "Write a report",
            [profile()],
            config(),
            negotiate=False,
        )
    assert len(model.calls) == 2


def service_for(tmp_path, model):
    return build_service(
        root=tmp_path,
        model=model,
        profiles={"planner": profile("planner", role="planner"), "writer": profile()},
        tool_definitions=[],
        executor=FixtureTools(tmp_path),
        verifier=StubVerifier(),
        approver=PolicyApprover(),
    )


async def test_unsupported_dataset_rules_fail_without_model_calls(tmp_path):
    model = ScriptedModel()
    result = await service_for(tmp_path, model).run_goal(
        "Test code",
        config(),
        goal_acceptance=[AcceptanceRule(type="pytest_passes", path="tests")],
    )
    assert result.failure_code == FailureCode.INVALID_OUTPUT
    assert "unsupported acceptance types" in result.failure_reason
    assert result.graph is None and result.tasks == [] and model.calls == []
    assert list(tmp_path.iterdir()) == []


async def test_composed_service_rejects_non_worker_assignees_before_execution(tmp_path):
    service = service_for(tmp_path, ScriptedModel())
    assert service.planner.preflight is service.scheduler.preflight
    with pytest.raises(PlanContractError, match="not a registered worker"):
        await service.run_graph(graph(task(agent="planner")), config())
    assert list(tmp_path.iterdir()) == []


async def test_invalid_child_contract_does_not_insert_or_suspend_parent(tmp_path):
    bad_child = task(
        "child",
        depends_on=["first"],
        inputs=[
            InputRef(from_task="first", path="undeclared.md", destination="inputs/brief.md"),
        ],
    )
    worker = ScriptedWorker(
        tmp_path,
        {
            "parent": [
                AgentOutcome(kind="delegate", children=[bad_child]),
            ]
        },
    )
    result = await Scheduler(worker, StubVerifier(), PolicyApprover(), root=tmp_path).run(
        graph(task("first"), task("parent", depends_on=["first"])),
        config(run_id="child-error"),
    )
    assert [t.task_id for t in result.tasks] == ["first", "parent"]
    assert result.tasks[-1].failure_code == FailureCode.INVALID_OUTPUT
    assert "not published by first" in result.tasks[-1].failure_reason
    assert [t.task_id for t, _ in worker.calls] == ["first", "parent"]
    assert not (tmp_path / ".agentverse-state/child-error/parent").exists()


def test_nested_archive_paths_are_preserved_without_rewriting_caller_rules():
    planned = linked_graph()
    planned.tasks[0].outputs = ["outputs/research/brief.md"]
    planned.tasks[0].acceptance[0].path = "outputs/research/brief.md"
    planned.tasks[-1].inputs[0].path = "research/brief.md"
    planned.tasks[-1].outputs = ["outputs/final/report.md"]
    planned.tasks[-1].acceptance[0].path = "outputs/final/report.md"
    planned.goal_acceptance[0].path = "final/report.md"
    before = planned.model_dump_json()
    PlanPreflight().validate(planned, config())
    assert planned.model_dump_json() == before


def test_additional_rule_requires_explicit_adapter_configuration():
    planned = graph(task())
    planned.tasks[0].acceptance = [AcceptanceRule(type="pytest_passes", path="tests")]
    with pytest.raises(PlanContractError, match="unsupported"):
        PlanPreflight().validate(planned, config())
    PlanPreflight(supported_acceptance_types={"file_exists", "pytest_passes"}).validate(
        planned,
        config(),
    )
    with pytest.raises(ValueError, match="known acceptance rules"):
        PlanPreflight(supported_acceptance_types={"invented_rule"})
