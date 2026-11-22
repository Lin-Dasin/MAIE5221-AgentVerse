"""Verify the Mid Test driver with a scripted discussion engine."""

import json
from pathlib import Path

import pytest

from bus.messages import MessageBus
from ui import cli, scenario

FIRST = "Build the booking system from scratch."
SECOND = "Extend that booking system with a dashboard."
AGENTS = ("lead", "dev", "qa", "release")


@pytest.fixture
def prepared(tmp_path: Path):
    packet = tmp_path / "scenario_1_software_engineering 2"
    packet.mkdir()
    role_text = "# Software Development Team\n" + "".join(
        f"\n## Role {agent} ({agent})\n\n```text\nTeacher prompt for {agent}.\n```\n"
        for agent in AGENTS
    )
    (packet / "01_role_initialization.md").write_text(role_text, encoding="utf-8")
    for name, text in (("02_task_1.md", FIRST), ("03_task_2.md", SECOND)):
        (packet / name).write_text(f"# Task\n\n```text\n{text}\n```\n", encoding="utf-8")
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "software.yaml").write_text(
        json.dumps(
            {
                "scenario_id": "scenario_1_software_engineering",
                "roles": {agent: ["file_write"] for agent in AGENTS},
            }
        ),
        encoding="utf-8",
    )
    run_dir = tmp_path / "session"
    assert cli.main(["mid-test", "init", str(packet), "--run-dir", str(run_dir)]) == 0
    session = scenario.ScenarioSession.model_validate_json((run_dir / "scenario.json").read_bytes())
    return run_dir, profiles, session


async def test_same_group_two_tasks_interleave_and_export(prepared, tmp_path):
    run_dir, profiles, session = prepared
    group = session.conversation_id
    bus = MessageBus(run_dir / "messages.sqlite3")

    async def engine(request, human, emit):
        assert tuple(request.profiles) == AGENTS
        assert request.profiles["lead"].system_prompt == "Teacher prompt for lead."
        assert request.conversation_id == group and request.run_id == request.workspace.name
        assert all(event.conversation_id == group for event in request.history)
        assert human() is None
        if request.task.task_id == "task-1":
            assert request.task.text == FIRST and request.inputs == ()
            with pytest.raises(RuntimeError, match="already has a running attempt"):
                await scenario.run_task(run_dir, 1, profiles, engine)
            emit("lead", "Which room should we use for verification?")
            assert (
                cli.main(["mid-test", "say", str(run_dir), "--text", "Use the orange room."]) == 0
            )
            emit("qa", "Checking availability and cancellation.")
            bus.publish(group, "instructor", "message", "Use March for the date range.")
            assert human() == "Use the orange room."
            assert human() == "Use March for the date range."
            assert human() is None
            emit("dev", "Published the booking data fixture.")
            (request.workspace / "outputs" / "booking.json").write_text("[55]", encoding="utf-8")
            (request.workspace / "outputs" / "scratch.txt").write_text("internal draft")
            return ("outputs/booking.json",)
        assert request.task.text == SECOND and request.inputs == ("booking.json",)
        assert [item.text for item in request.history if item.sender == "instructor"] == [
            FIRST,
            "Use the orange room.",
            "Use March for the date range.",
            SECOND,
        ]
        assert (request.workspace / "inputs" / "booking.json").read_text() == "[55]"
        assert sorted(path.name for path in (request.workspace / "inputs").iterdir()) == [
            "booking.json"
        ]
        emit("release", "The dashboard uses the published booking fixture.")
        (request.workspace / "outputs" / "dashboard.html").write_text("<p>55</p>")
        return ("outputs/dashboard.html",)

    first = await scenario.run_task(run_dir, 1, profiles, engine)
    assert first is not None and (first / "booking.json").is_file()
    assert not (first / "scratch.txt").exists()
    with pytest.raises(FileExistsError, match="already accepted"):
        await scenario.run_task(run_dir, 1, profiles, engine)
    second = await scenario.run_task(run_dir, 2, profiles, engine)
    assert second is not None and (second / "dashboard.html").is_file()

    destination = tmp_path / "submission"
    assert cli.main(["mid-test", "export", str(run_dir), "--output-dir", str(destination)]) == 0
    transcript = (destination / "transcript.md").read_text(encoding="utf-8")
    assert (
        transcript.index(FIRST)
        < transcript.index("Use the orange room.")
        < transcript.index(SECOND)
    )
    assert "scenario_inputs_ready" in transcript and "inputs/booking.json" in transcript
    assert "internal draft" not in transcript and str(run_dir) not in transcript
    assert (destination / "artifacts" / "task-1" / "booking.json").read_text() == "[55]"
    assert (destination / "artifacts" / "task-2" / "dashboard.html").read_text() == "<p>55</p>"


async def test_task_order_and_retry_with_no_partial_publication(prepared):
    run_dir, profiles, session = prepared
    bus = MessageBus(run_dir / "messages.sqlite3")

    async def rejected(request, human, emit):
        (request.workspace / "internal.txt").write_text("not an accepted output")
        return ("outputs/../internal.txt",)

    with pytest.raises(ValueError, match="accept Task 1"):
        await scenario.run_task(run_dir, 2, profiles, rejected)
    with pytest.raises(ValueError, match="Accept both tasks"):
        scenario.export_session(run_dir, run_dir.parent / "incomplete-submission")
    assert bus.history(session.conversation_id, "instructor") == []
    with pytest.raises(ValueError):
        await scenario.run_task(run_dir, 1, profiles, rejected)
    assert not (run_dir / "tasks" / "task-1" / "accepted").exists()

    async def retried(request, human, emit):
        assert not (request.workspace / "internal.txt").exists()
        (request.workspace / "outputs" / "ready.txt").write_text("ready")
        return ("outputs/ready.txt",)

    accepted = await scenario.run_task(run_dir, 1, profiles, retried)
    assert accepted is not None and (accepted / "ready.txt").read_text() == "ready"
    assert [row.text for row in bus.history(session.conversation_id, "instructor")] == [FIRST]
