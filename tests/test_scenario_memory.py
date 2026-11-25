"""Private per-role scenario memory: isolation, recall and generic lessons."""

import json
from types import SimpleNamespace

from memory.scenario_memory import ScenarioMemory
from protocol.delivery import RequirementAssessment
from protocol.models import AgentProfile
from ui.scenario_delivery import _distill_lessons, _inject_memory


def _profile(agent_id: str) -> AgentProfile:
    return AgentProfile(
        agent_id=agent_id, role="worker", system_prompt=f"You are {agent_id}.", tools=[]
    )


def test_role_memory_files_are_isolated_and_events_are_logged(tmp_path):
    memory = ScenarioMemory(tmp_path)
    alpha = memory.write(
        "alpha",
        task_id="task-1",
        outcome="passed",
        lessons=[("Run code_exec before finalizing.", ("code", "verification"))],
    )
    beta = memory.write(
        "beta",
        task_id="task-1",
        outcome="failed",
        lessons=[("Cite only returned URLs and excerpts.", ("search", "evidence"))],
    )

    assert memory.path_for("alpha") != memory.path_for("beta")
    alpha_text = memory.path_for("alpha").read_text(encoding="utf-8")
    beta_text = memory.path_for("beta").read_text(encoding="utf-8")
    assert "Run code_exec" in alpha_text and "Cite only" not in alpha_text
    assert "Cite only" in beta_text and "Run code_exec" not in beta_text
    assert memory.recall("alpha", "code review")[0].entry_id == alpha.entry_id
    assert memory.recall("beta", "source evidence")[0].entry_id == beta.entry_id
    memory.log_event("memory_write", "alpha", "task-1", [alpha.entry_id])
    memory.log_event("memory_write", "beta", "task-1", [beta.entry_id])

    events = [
        json.loads(line)
        for line in memory.events_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [event["phase"] for event in events] == ["memory_write", "memory_write"]
    assert {event["agent_id"] for event in events} == {"alpha", "beta"}


def test_identical_lessons_update_in_place_and_recall_is_unique(tmp_path):
    memory = ScenarioMemory(tmp_path)
    lessons = [
        ("Run code_exec before finalizing.", ("code",)),
        ("Read the target first.", ("handoff",)),
    ]
    first = memory.write("writer", task_id="task-1", outcome="failed", lessons=lessons)
    second = memory.write(
        "writer", task_id="task-2", outcome="passed", lessons=list(reversed(lessons))
    )
    third = memory.write("writer", task_id="task-2", outcome="passed", lessons=lessons)

    assert first.entry_id == second.entry_id == third.entry_id
    entries = json.loads(memory.path_for("writer").read_text(encoding="utf-8"))["entries"]
    assert len(entries) == 1
    assert entries[0]["outcome"] == "passed"

    memory.write(
        "writer",
        task_id="task-2",
        outcome="passed",
        lessons=[("Validate aggregation with sample data.", ("aggregation",))],
    )
    recalled = memory.recall("writer", "code aggregation", limit=3)
    texts = [" | ".join(entry.lessons) for entry in recalled]
    assert len(texts) == len(set(texts))
    assert any("Run code_exec" in text for text in texts)


def test_task_two_recalls_only_the_agents_own_lessons(tmp_path):
    memory = ScenarioMemory(tmp_path)
    memory.write(
        "alpha",
        task_id="task-1",
        outcome="passed",
        lessons=[("Run code_exec before finalizing.", ("code", "verification"))],
    )
    memory.write(
        "beta",
        task_id="task-1",
        outcome="passed",
        lessons=[("Cite only returned URLs and excerpts.", ("search", "evidence"))],
    )
    profiles = {"alpha": _profile("alpha"), "beta": _profile("beta")}

    recalled = _inject_memory(profiles, memory, "task-2", "code review task")

    assert set(recalled) == {"alpha", "beta"}
    assert "PRIVATE LESSONS" in profiles["alpha"].system_prompt
    assert "Run code_exec" in profiles["alpha"].system_prompt
    assert "Cite only" not in profiles["alpha"].system_prompt
    assert "PRIVATE LESSONS" in profiles["beta"].system_prompt
    assert "Cite only" in profiles["beta"].system_prompt
    assert "Run code_exec" not in profiles["beta"].system_prompt
    events = memory.events_path.read_text(encoding="utf-8").splitlines()
    assert any(json.loads(line)["phase"] == "memory_recall" for line in events)


def test_lessons_are_generic_and_do_not_copy_task_specific_facts():
    attempt = SimpleNamespace(
        assessments=[
            RequirementAssessment(
                requirement_id="dashboard",
                owner="dev",
                status="failed",
                defects=[
                    "usage_dashboard.py did not serve / on the requested --host, --port and --db.",
                    "The first day has two active starts. Exclude cancellations.",
                    "FOMC statement and Boardroom data are stale.",
                ],
            )
        ]
    )
    lessons = _distill_lessons("dev", SimpleNamespace(attempts=[attempt]), {"code_exec"})
    text = " ".join(lesson for lesson, _ in lessons)

    assert "Run the program with the requested host/port/database" in text
    assert "Validate aggregation semantics" in text
    assert "FOMC" not in text and "Boardroom" not in text
    assert len(lessons) <= 3