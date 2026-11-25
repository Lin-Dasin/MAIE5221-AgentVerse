"""An unknown two-task scenario runs through the generic managed path."""

import json

from protocol.models import ModelResponse, ToolCall
from tests.test_discussion import ScriptedModel
from ui import scenario
from ui.scenario_delivery import managed_discussion_engine

ROLES = {
    "coordinator": ["file_read", "file_write"],
    "writer": ["file_read", "file_write"],
}
TASK_ONE = "Write a brief to outputs/topic-brief.md."
TASK_TWO = "Analyze the brief with outputs/analyze.py and save outputs/findings.md."
REQUIRED = {
    1: ("outputs/topic-brief.md",),
    2: ("outputs/analyze.py", "outputs/findings.md"),
}
CANDIDATES = {
    1: ("outputs/writer/topic-brief.md",),
    2: ("outputs/writer/analyze.py", "outputs/writer/findings.md"),
}
CONTENT = {
    "outputs/notes.md": "Coordinate the work and hand it to the writer.\n",
    "outputs/topic-brief.md": "Five verified facts about the topic.\n",
    "outputs/analyze.py": "values = [1, 2, 3]\nprint(sum(values))\n",
    "outputs/findings.md": "The values sum to six.\n",
}


def _write(*paths):
    return ModelResponse(
        tool_calls=[
            ToolCall(
                call_id=f"write-{index}-{path}",
                name="file_write",
                arguments={"path": path, "text": CONTENT[path]},
            )
            for index, path in enumerate(paths)
        ],
        tokens=20,
    )


def _final(role, text):
    return ModelResponse(content=f"{role}: {text}", tokens=20)


def _prepare(tmp_path):
    packet = tmp_path / "blind"
    packet.mkdir()
    roles = "".join(f"## {role} ({role})\n```text\nYou are {role}.\n```\n" for role in ROLES)
    (packet / "01_role_initialization.md").write_text("# Blind\n" + roles, encoding="utf-8")
    (packet / "02_task_1.md").write_text(f"```text\n{TASK_ONE}\n```\n", encoding="utf-8")
    (packet / "03_task_2.md").write_text(f"```text\n{TASK_TWO}\n```\n", encoding="utf-8")
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "blind.yaml").write_text(
        json.dumps({"scenario_id": "blind", "roles": ROLES}), encoding="utf-8"
    )
    session = tmp_path / "session"
    scenario.init_session(packet, session)
    return session, profiles


async def test_generic_managed_scenario_accepts_both_tasks_and_records_metrics(tmp_path):
    session, profiles = _prepare(tmp_path)
    first_model = ScriptedModel(
        _write("outputs/notes.md"),
        _final("coordinator", "The writer will produce the brief."),
        _write(*REQUIRED[1]),
        _final("writer", "The research brief is saved."),
    )
    first = managed_discussion_engine(
        first_model,
        scenario_id="blind",
        number=1,
        max_rounds=1,
        max_task_tokens=60_000,
        max_output_tokens=1024,
        max_rework_rounds=1,
    )
    assert await scenario.run_task(session, 1, profiles, first) is None
    assert not first_model.responses

    task_one = session / "tasks/task-1"
    (attempt,) = (task_one / "attempts").iterdir()
    summary = json.loads((attempt / "discussion-summary.json").read_bytes())
    assert summary["mode"] == "managed" and summary["kind"] == "generic"
    assert summary["status"] == "passed" and summary["stop_reason"] == "accepted"
    assert summary["generic_checks"]["research_required"] is False
    assert set(summary["candidates"]) == set(CANDIDATES[1])
    assert summary["memory"]["written"].keys() == ROLES.keys()
    assert not (task_one / "accepted").exists()
    scenario.accept_attempt(session, 1, attempt.name, tuple(summary["candidates"]))

    second_model = ScriptedModel(
        _write("outputs/notes.md"),
        _final("coordinator", "The writer will run the analysis."),
        _write("outputs/analyze.py"),
        _final("writer", "The script is saved; I will add the findings next."),
        _write("outputs/findings.md"),
        _final("writer", "The analysis and findings are saved."),
    )
    second = managed_discussion_engine(
        second_model,
        scenario_id="blind",
        number=2,
        max_rounds=1,
        max_task_tokens=60_000,
        max_output_tokens=1024,
        max_rework_rounds=1,
    )
    assert await scenario.run_task(session, 2, profiles, second) is None
    assert not second_model.responses

    task_two = session / "tasks/task-2"
    (attempt_two,) = (task_two / "attempts").iterdir()
    summary_two = json.loads((attempt_two / "discussion-summary.json").read_bytes())
    assert summary_two["status"] == "passed"
    assert summary_two["generic_checks"] == {
        "research_required": False,
        "search_calls": 0,
        "search_ok": True,
    }
    assert set(summary_two["candidates"]) == set(CANDIDATES[2])
    accepted = scenario.accept_attempt(
        session, 2, attempt_two.name, tuple(summary_two["candidates"])
    )
    assert (accepted / "writer/analyze.py").is_file()

    metrics = json.loads((session / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["summary"]["tasks_total"] == 2
    assert metrics["summary"]["tasks_passed"] == 2
    assert metrics["summary"]["tasks_accepted"] == 2
    assert metrics["summary"]["task_completion_rate"] == 1.0
    assert metrics["summary"]["scenario_completed"] is True
    assert metrics["summary"]["execution_time_sec"] > 0
    assert {task["status"] for task in metrics["tasks"]} == {"passed"}

    for role in ROLES:
        assert (session / "memory" / f"{role}.json").is_file()
    events = [
        json.loads(line)
        for line in (session / "memory/events.jsonl").read_text().splitlines()
    ]
    written = {
        (row["phase"], row["task_id"])
        for row in events
        if row["phase"] == "memory_write"
    }
    assert written == {("memory_write", "task-1"), ("memory_write", "task-2")}