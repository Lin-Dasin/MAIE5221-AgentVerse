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
    model = ScriptedModel(*[invalid.model_dump_json()] * 3)
    with pytest.raises(PlanningError, match="not published by final task"):
        await Planner(model, profile("planner", role="planner")).plan(
            "Write a report",
            [profile()],
            config(),
            negotiate=False,
        )
    assert len(model.calls) == 3


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
    result = await service.run_graph(graph(task(agent="planner")), config())
    assert result.failure_code == FailureCode.INVALID_OUTPUT
    assert "not a registered worker" in result.failure_reason
    assert result.tasks == []
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


async def test_third_generation_repairs_path_and_runs_real_handoffs(tmp_path):
    from harness.verdict import FileVerifier
    from protocol.models import ToolCall
    from tests.test_runtime import write_tool

    valid = linked_graph()
    invalid = valid.model_copy(deep=True)
    break_contract(invalid, "input")
    model = ScriptedModel(
        invalid.model_dump_json(),
        invalid.model_dump_json(),
        valid.model_dump_json(),
        ModelResponse(
            tool_calls=[
                ToolCall(
                    call_id="first",
                    name="file_write",
                    arguments={
                        "path": "outputs/first.md",
                        "text": "original upstream text",
                    },
                )
            ]
        ),
        "First file ready",
        ModelResponse(
            tool_calls=[
                ToolCall(
                    call_id="last",
                    name="file_write",
                    arguments={
                        "path": "outputs/last.md",
                        "text": "final report",
                    },
                )
            ]
        ),
        "Last file ready",
    )
    service = build_service(
        root=tmp_path,
        model=model,
        profiles={
            "planner": profile("planner", role="planner"),
            "writer": profile(tools=["file_write"]),
        },
        tool_definitions=[write_tool()],
        executor=FixtureTools(tmp_path),
        verifier=FileVerifier(),
        approver=PolicyApprover(),
    )
    result = await service.run_goal("Write a two-task report", config(max_planning_calls=3))
    assert result.goal_verdict.passed
    assert len(result.tasks) == 2
    assert (tmp_path / "workspaces/writer/inputs/brief.md").read_text() == "original upstream text"
    assert "not published by first" in model.calls[2][0][-1].content


async def test_bids_reserve_three_final_generation_calls():
    valid = linked_graph()
    invalid = valid.model_copy(deep=True)
    break_contract(invalid, "goal")
    bid = '{"task_ids":["last"],"rationale":"Can write"}'
    model = ScriptedModel(
        valid.model_dump_json(),
        bid,
        bid,
        invalid.model_dump_json(),
        invalid.model_dump_json(),
        valid.model_dump_json(),
    )
    result = await Planner(model, profile("planner", role="planner")).plan(
        "Write a report",
        [profile(), profile("other"), profile("third")],
        config(max_planning_calls=6),
    )
    assert result.tasks == valid.tasks and len(model.calls) == 6
    assert "Specialist bids" in result.summary
    assert "third requested" not in result.summary


async def test_budget_still_caps_repair_calls():
    model = ScriptedModel("invalid", "invalid")
    with pytest.raises(PlanningError, match="budget"):
        await Planner(model, profile("planner", role="planner")).plan(
            "Write a report",
            [profile()],
            config(max_planning_calls=2),
            negotiate=False,
        )
    assert len(model.calls) == 2


async def test_node_acceptance_path_repaired_before_execution():
    valid = linked_graph()
    invalid = valid.model_copy(deep=True)
    invalid.tasks[-1].acceptance[0].path = "last.md"
    model = ScriptedModel(invalid.model_dump_json(), valid.model_dump_json())
    result = await Planner(model, profile("planner", role="planner")).plan(
        "Write a report",
        [profile()],
        config(),
        negotiate=False,
    )
    assert result == valid
    assert "use declared workspace output 'outputs/last.md'" in model.calls[1][0][-1].content


@pytest.mark.parametrize("path", ["inputs/brief.md", "auxiliary.md"])
def test_workspace_checks_are_not_rewritten(path):
    planned = linked_graph()
    planned.tasks[-1].acceptance = [AcceptanceRule(type="file_exists", path=path)]
    before = planned.model_dump_json()
    PlanPreflight().validate(planned, config())
    assert planned.model_dump_json() == before


def test_declared_input_is_not_mistaken_for_nested_output():
    planned = linked_graph()
    planned.tasks[-1].outputs.append("outputs/inputs/brief.md")
    planned.tasks[-1].acceptance.append(AcceptanceRule(type="file_exists", path="inputs/brief.md"))
    PlanPreflight().validate(planned, config())
