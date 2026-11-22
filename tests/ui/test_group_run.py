"""Verify group task dispatch and conversation records without calling an API."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bus.messages import MessageBus
from harness.cases import load_suite
from harness.offline import build_offline_service
from protocol.models import AcceptanceRule, FailureCode, InputRef, RunConfig, Task, TaskGraph
from ui import cli, group_run

GOAL = "让两位成员先整理素材，再合成简报。"


@pytest.fixture
def bus(tmp_path):
    bus = MessageBus(tmp_path / "messages.sqlite3")
    bus.create_conversation("room", "group", ["user", "writer", "reviewer", "orchestrator"])
    bus.create_conversation("other", "group", ["user", "writer", "orchestrator"])
    bus.create_conversation("direct", "private", ["user", "orchestrator"])
    return bus


def command(bus, tmp_path, *, conversation="room", sender="user", notifier=None):
    argv = [
        "messages",
        "--db",
        str(bus.database),
        "run",
        conversation,
        "--sender",
        sender,
        "--goal",
        GOAL,
        "--work-dir",
        str(tmp_path / "runs"),
    ]
    if notifier is not None:
        argv += ["--notifier", notifier]
    return argv


def test_group_run_writes_request_assignment_handoff_and_result_to_the_same_room(
    bus, tmp_path, monkeypatch, capsys
):
    acceptance = tmp_path / "rules.json"
    acceptance.write_text(
        json.dumps([{"type": "file_exists", "path": "summary.md"}]),
        encoding="utf-8",
    )
    profiles = tmp_path / "profiles"
    runs = []
    source = InputRef(from_task="notes", path="notes.md", destination="inputs/notes.md")
    graph = TaskGraph(
        tasks=[
            Task(
                task_id="notes",
                title="Prepare notes",
                assignee="reviewer",
                outputs=["outputs/notes.md"],
                acceptance=[AcceptanceRule(type="file_exists", path="outputs/notes.md")],
            ),
            Task(
                task_id="summary-task",
                title="Summarize notes",
                assignee="writer",
                depends_on=["notes"],
                inputs=[source],
                outputs=["outputs/summary.md"],
                acceptance=[AcceptanceRule(type="file_exists", path="outputs/summary.md")],
            ),
        ],
        final_task_id="summary-task",
        goal_acceptance=[AcceptanceRule(type="file_exists", path="summary.md")],
    )

    async def fake_goal(
        goal,
        work_dir,
        *,
        rules,
        profiles_dir,
        run_id,
        worker_ids,
        trace_factory,
        ask,
        approval_timeout_sec,
    ):
        runs.append(run_id)
        assert goal == GOAL
        assert work_dir == tmp_path / "runs"
        assert rules == [AcceptanceRule(type="file_exists", path="summary.md")]
        assert profiles_dir == profiles
        assert set(worker_ids) == {"writer", "reviewer"}
        assert callable(ask)
        assert approval_timeout_sec == 60
        root = work_dir / run_id
        trace = trace_factory(root / "trace.jsonl")
        await trace.plan_ready(run_id, graph)
        await trace.artifacts_handed_off(run_id, "summary-task", [source])
        return root / "evaluation.json", SimpleNamespace(
            run_id=run_id,
            status="success",
            artifacts=["artifacts/summary.md"],
            trace_path="trace.jsonl",
            subtasks_passed=2,
            subtasks_total=2,
            warnings=[],
            failure_reason=None,
            raw_result=SimpleNamespace(failure_code=None),
            duration_sec=1.25,
        )

    monkeypatch.setattr(group_run, "run_goal", fake_goal)
    argv = command(bus, tmp_path) + [
        "--profiles-dir",
        str(profiles),
        "--acceptance",
        str(acceptance),
    ]
    assert cli.main(argv) == 0
    output = capsys.readouterr()
    assert not output.err
    (run_id,) = runs
    assert run_id in output.out

    reopened = MessageBus(bus.database)
    history = reopened.history("room", "reviewer")
    assert [(event.sequence, event.sender, event.kind) for event in history] == [
        (1, "user", "message"),
        (2, "orchestrator", "assignment"),
        (3, "orchestrator", "handoff"),
        (4, "orchestrator", "message"),
    ]
    request, assignment, handoff, outcome = [json.loads(event.text) for event in history]
    assert {row["run_id"] for row in (request, assignment, handoff, outcome)} == {run_id}
    assert request["goal"] == GOAL
    assert assignment == {
        "event": "tasks_assigned",
        "run_id": run_id,
        "final_task_id": "summary-task",
        "tasks": [
            {"task_id": "notes", "agent_id": "reviewer", "depends_on": []},
            {"task_id": "summary-task", "agent_id": "writer", "depends_on": ["notes"]},
        ],
    }
    assert handoff["task_id"] == "summary-task" and handoff["agent_id"] == "writer"
    assert handoff["inputs"] == [
        {"from_task": "notes", "path": "notes.md", "destination": "inputs/notes.md"}
    ]
    assert outcome["status"] == "success" and outcome["subtasks_passed"] == 2
    root = tmp_path / "runs" / run_id
    assert outcome["artifacts"] == [str(root / "artifacts" / "summary.md")]
    assert outcome["evaluation"] == str(root / "evaluation.json")
    assert reopened.history("room", "user") == history
    assert reopened.history("other", "user") == []

    trace_path = root / "trace.jsonl"
    trace = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert [event["event"] for event in trace] == ["plan_ready", "artifacts_handed_off"]
    assert {event["run_id"] for event in trace} == {run_id}


async def test_scheduler_broadcasts_child_assignment_with_metadata_only(bus, tmp_path):
    bus.create_conversation("delegation", "group", ["user", "writer", "researcher", "orchestrator"])
    suite = load_suite(Path(__file__).resolve().parents[2] / "harness" / "suites" / "dev.json")
    case = next(case for case in suite.cases if case.task_id == "p3-delegation")
    case.graph.summary = "raw negotiator rationale"
    root = tmp_path / "delegation-run"
    trace = group_run.GroupTrace(root / "trace.jsonl", bus, "delegation", "orchestrator")
    service = build_offline_service(case, root, observer=trace)
    config = RunConfig(memory_enabled=False, write_memory=False)

    result = await service.run_graph(case.graph, config)

    assert result.goal_verdict.passed
    history = bus.history("delegation", "researcher")
    assert [event.kind for event in history] == ["assignment", "delegation", "handoff"]
    assignment, delegation, handoff = [json.loads(event.text) for event in history]
    assert assignment["tasks"] == [{"task_id": "report", "agent_id": "writer", "depends_on": []}]
    assert delegation == {
        "event": "tasks_delegated",
        "run_id": result.run_id,
        "task_id": "report",
        "agent_id": "writer",
        "children": [{"task_id": "research", "agent_id": "researcher"}],
    }
    assert handoff["inputs"][0]["from_task"] == "research"
    assert {event["run_id"] for event in (assignment, delegation, handoff)} == {result.run_id}
    assert bus.history("delegation", "user") == history
    assert bus.history("other", "user") == []
    assert bus.history("direct", "user") == []
    assert all(
        text not in event.text
        for event in history
        for text in (case.graph.summary, "Offline fixture:", "# Parent draft")
    )


@pytest.mark.parametrize(
    ("conversation", "sender", "notifier"),
    [
        ("direct", "user", None),
        ("room", "outsider", None),
        ("room", "user", "outsider"),
        ("room", "user", "user"),
    ],
)
def test_group_run_rejects_invalid_room_or_members_before_starting(
    bus, tmp_path, monkeypatch, conversation, sender, notifier
):
    async def unexpected(*args, **kwargs):
        pytest.fail("Invalid group participants must not launch a goal")

    monkeypatch.setattr(group_run, "run_goal", unexpected)
    assert (
        cli.main(
            command(bus, tmp_path, conversation=conversation, sender=sender, notifier=notifier)
        )
        == 2
    )
    assert bus.history(conversation, "user") == []


@pytest.mark.parametrize(("error_before_report", "expected_exit"), [(False, 1), (True, 2)])
def test_group_run_records_unsuccessful_attempt(
    bus, tmp_path, monkeypatch, error_before_report, expected_exit
):
    internal_detail = "internal provider diagnostic"
    report_reason = "Missing final report"

    async def fail_goal(goal, work_dir, *, run_id, **kwargs):
        if error_before_report:
            raise RuntimeError(internal_detail)
        return work_dir / run_id / "evaluation.json", SimpleNamespace(
            run_id=run_id,
            status="failed",
            artifacts=[],
            trace_path=None,
            subtasks_passed=1,
            subtasks_total=2,
            warnings=[internal_detail],
            failure_reason=report_reason,
            raw_result=SimpleNamespace(failure_code=FailureCode.INVALID_OUTPUT),
            duration_sec=1.25,
        )

    monkeypatch.setattr(group_run, "run_goal", fail_goal)
    assert cli.main(command(bus, tmp_path)) == expected_exit
    request, outcome = bus.history("room", "user")
    assert request.sender == "user" and GOAL in request.text
    assert outcome.sender == "orchestrator"
    assert "fail" in outcome.text.lower()
    assert internal_detail not in outcome.text
    if not error_before_report:
        assert report_reason not in outcome.text
        assert json.loads(outcome.text)["failure_code"] == "invalid_output"
        assert json.loads(outcome.text)["warning_count"] == 1
