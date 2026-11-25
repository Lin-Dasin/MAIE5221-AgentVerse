"""Exercise model-only repair on the two managed software tasks."""

import json
from collections import deque

import pytest

from harness import scenario_software as software
from protocol.models import ModelResponse, ToolCall
from ui import scenario


@pytest.mark.parametrize("number", [1, 2])
async def test_managed_software_repairs_only_dev_then_waits_for_final_review(
    tmp_path, monkeypatch, number
):
    pytest.importorskip("orchestrator.delivery")
    from ui.scenario_delivery import SOFTWARE, managed_discussion_engine

    roles = ("lead", "dev", "qa", "release")
    packet = tmp_path / "scenario_1_software_engineering"
    packet.mkdir()
    role_text = "".join(f"## {role} ({role})\n```text\nYou are {role}.\n```\n" for role in roles)
    (packet / "01_role_initialization.md").write_text("# Booking\n" + role_text, encoding="utf-8")
    for path, task in (
        ("02_task_1.md", "Build a room booking application."),
        ("03_task_2.md", "Add a dashboard for room usage."),
    ):
        (packet / path).write_text(f"```text\n{task}\n```\n", encoding="utf-8")
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "software.yaml").write_text(
        json.dumps(
            {
                "scenario_id": "scenario_1_software",
                "roles": dict.fromkeys(roles, ["file_read", "file_write"]),
            }
        ),
        encoding="utf-8",
    )
    session = tmp_path / "run"
    scenario.init_session(packet, session)
    if number == 2:

        async def approved_booking(request, human, emit):
            booking = request.workspace / "outputs/dev/booking_app.py"
            booking.parent.mkdir()
            booking.write_bytes(b"unchanged booking input")
            return ("outputs/dev/booking_app.py",)

        await scenario.run_task(session, 1, profiles, approved_booking)
    program, guide, check_name = (
        (software.BOOKING_ARTIFACT, software.BOOKING_GUIDE_ARTIFACT, "check_booking")
        if number == 1
        else (software.DASHBOARD_ARTIFACT, software.DASHBOARD_GUIDE_ARTIFACT, "check_dashboard")
    )
    defect = f"In {guide.split('/')[-1]}, document the actual application's controls."

    def check(root, booking_path=None):
        if number == 2:
            assert booking_path.read_bytes() == b"unchanged booking input"
        assert (root / program).read_bytes() == b"stable code"
        if (root / guide).read_bytes() == b"rewritten guide":
            return software.SoftwareCheck("passed", evidence=["guide reconciled"])
        return software.SoftwareCheck("failed", [defect], evidence=["runtime verified"])

    monkeypatch.setattr(software, check_name, check)

    def writes(*pairs):
        return ModelResponse(
            tool_calls=[
                ToolCall(
                    call_id=f"write-{index}",
                    name="file_write",
                    arguments={"path": path, "text": text},
                )
                for index, (path, text) in enumerate(pairs)
            ],
            tokens=50,
        )

    handoff = ModelResponse(content="Saved the candidate.", tokens=50)
    replies = deque(
        [
            ("lead", writes(("outputs/lead.md", "Implementation guidance"))),
            ("lead", handoff),
            ("dev", writes((program, "stable code"), (guide, "old guide"))),
            ("dev", handoff),
            ("qa", writes(("outputs/qa.md", "Quality review"))),
            ("qa", handoff),
            ("release", writes(("outputs/release.md", "Release review"))),
            ("release", handoff),
            ("dev", writes((program, "unneeded rewrite"), (guide, "rewritten guide"))),
            ("dev", handoff),
        ]
    )
    repairs = []

    class Model:
        async def complete(self, messages, tools, *, max_tokens):
            role, reply = replies.popleft()
            assert f"You are {role}." in messages[0].content
            if "Rework attempt: 1/1" in messages[0].content:
                assert defect in messages[0].content
                repairs.append(role)
            return reply

    engine = managed_discussion_engine(
        Model(),
        scenario_id=SOFTWARE,
        number=number,
        max_task_tokens=180_000,
        max_output_tokens=1024,
        max_rework_rounds=1,
    )
    assert await scenario.run_task(session, number, profiles, engine) is None
    assert not replies and repairs and set(repairs) == {"dev"}
    task = session / f"tasks/task-{number}"
    (attempt,) = (task / "attempts").iterdir()
    summary = json.loads((attempt / "discussion-summary.json").read_bytes())
    assert summary["status"] == "passed" and len(summary["attempts"]) == 2
    assert not (task / "accepted").exists()
    accepted = scenario.accept_attempt(session, number, attempt.name, tuple(summary["candidates"]))
    assert (accepted / "dev" / guide.split("/")[-1]).read_bytes() == b"rewritten guide"
