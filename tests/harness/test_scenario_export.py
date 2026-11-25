import json
import shutil

import pytest

from bus.messages import MessageEvent
from harness.scenario_export import export_scenario

CONVERSATION = "mid-fixture"
TIMESTAMP = "2026-11-22T09:00:00+00:00"


def event(sequence, sender, text, *, kind="message", conversation=CONVERSATION):
    if isinstance(text, dict):
        text = json.dumps(text, ensure_ascii=False)
    return MessageEvent(conversation, sequence, sender, kind, text, TIMESTAMP)


@pytest.fixture
def artifacts(tmp_path):
    first = tmp_path / "accepted-task-one"
    first.mkdir()
    (first / "report.md").write_text("# Accepted first task", encoding="utf-8")
    second = tmp_path / "accepted-task-two"
    (second / "nested").mkdir(parents=True)
    (second / "nested" / "chart.png").write_bytes(b"\x89PNG\x00\xff")
    return {"task-1": first, "task-2": second}


def export(destination, artifacts, messages=()):
    return export_scenario(
        destination,
        scenario_id="scenario-1",
        conversation_id=CONVERSATION,
        roles={"lead": "Technical Lead", "writer": "编辑"},
        messages=messages,
        accepted_artifacts=artifacts,
    )


def test_export_preserves_public_order_and_safe_metadata_in_a_portable_bundle(tmp_path, artifacts):
    messages = [
        event(2, "lead", "收到\n```\n<script>example</script>"),
        event(1, "instructor", "Please begin"),
        event(
            3,
            "orchestrator",
            {
                "event": "tasks_assigned",
                "run_id": "first-run",
                "tasks": [
                    {
                        "task_id": "draft",
                        "agent_id": "lead",
                        "depends_on": [],
                        "tool_payload": "hidden-tool-payload",
                    }
                ],
            },
            kind="assignment",
        ),
        event(
            4,
            "orchestrator",
            {
                "event": "tasks_delegated",
                "task_id": "draft",
                "children": [{"task_id": "review", "agent_id": "writer"}],
            },
            kind="delegation",
        ),
        event(
            5,
            "orchestrator",
            {
                "event": "scenario_inputs_ready",
                "from_task": "task-1",
                "task_id": "task-2",
                "inputs": [
                    {
                        "from_task": "task-1",
                        "path": "report.md",
                        "destination": "inputs/report.md",
                    },
                    {"path": "/machine/private.txt", "destination": "../escape"},
                ],
            },
            kind="handoff",
        ),
        event(
            6,
            "orchestrator",
            {
                "event": "clarification_requested",
                "task_id": "review",
                "question": "Which diagram should be used?",
            },
        ),
        event(
            7,
            "instructor",
            {"event": "clarification_answered", "answer": "Use the simple diagram"},
        ),
        event(
            8,
            "orchestrator",
            {
                "event": "run_finished",
                "status": "success",
                "trace": "/machine/trace.jsonl",
                "evaluation": "/machine/evaluation.json",
            },
        ),
        event(9, "lead", "raw-tool-event", kind="tool"),
        event(10, "lead", {"role": "tool", "content": "raw-tool-response"}),
        event(11, "lead", {"tool_results": [{"content": "raw-tool-results"}]}),
    ]
    destination = tmp_path / "export"
    assert export(destination, artifacts, messages) == destination
    relocated = tmp_path / "relocated"
    destination.rename(relocated)

    manifest = json.loads((relocated / "manifest.json").read_bytes())
    assert manifest["scenario_id"] == "scenario-1"
    assert manifest["conversation_id"] == CONVERSATION
    transcript = (relocated / manifest["transcript"]).read_text(encoding="utf-8")
    entries = ("Please begin", "收到", "tasks_assigned", "tasks_delegated", "scenario_inputs_ready")
    assert [transcript.index(entry) for entry in entries] == sorted(
        transcript.index(entry) for entry in entries
    )
    assert "Technical Lead (lead)" in transcript
    assert "````text" in transcript
    assert '"destination": "inputs/report.md"' in transcript
    assert "Which diagram should be used?" in transcript
    assert "Use the simple diagram" in transcript
    assert '"status": "success"' in transcript
    for unsafe in ("hidden-tool-payload", "/machine/", "../escape", "raw-tool"):
        assert unsafe not in transcript
    first, second = (relocated / manifest["artifacts"][task] for task in ("task-1", "task-2"))
    assert (first / "report.md").read_bytes() == (artifacts["task-1"] / "report.md").read_bytes()
    assert (second / "nested/chart.png").read_bytes() == b"\x89PNG\x00\xff"


def test_export_rejects_missing_artifacts_and_preserves_existing_destination(tmp_path, artifacts):
    destination = tmp_path / "export"
    with pytest.raises(ValueError, match="task-1 and task-2"):
        export(destination, {"task-1": artifacts["task-1"]})
    assert not destination.exists()

    destination.mkdir()
    (destination / "sentinel").write_text("existing", encoding="utf-8")
    with pytest.raises(FileExistsError):
        export(destination, artifacts)
    assert [item.name for item in destination.iterdir()] == ["sentinel"]


def test_export_rejects_cross_conversation_and_duplicate_sequences(tmp_path, artifacts):
    destination = tmp_path / "export"
    mixed = [event(1, "instructor", "Public"), event(2, "lead", "Private", conversation="other")]
    with pytest.raises(ValueError, match="belong to the scenario"):
        export(destination, artifacts, mixed)
    duplicate = [event(1, "instructor", "First"), event(1, "lead", "Second")]
    with pytest.raises(ValueError, match="must be unique"):
        export(destination, artifacts, duplicate)
    assert not destination.exists()


def test_export_rejects_destination_inside_source_directory(tmp_path, artifacts):
    destination = artifacts["task-1"] / "subtree" / "export"
    with pytest.raises(ValueError, match="outside the accepted artifact"):
        export(destination, artifacts)
    assert not destination.parent.exists()


def test_export_removes_staging_after_copy_failure(tmp_path, artifacts, monkeypatch):
    copy = shutil.copyfile

    def fail_second_tree(source, target):
        if source.suffix == ".png":
            raise OSError("fixture copy failed")
        return copy(source, target)

    monkeypatch.setattr(shutil, "copyfile", fail_second_tree)
    with pytest.raises(OSError, match="fixture copy failed"):
        export(tmp_path / "export", artifacts)
    assert not (tmp_path / "export").exists()
    assert not list(tmp_path.glob(".scenario-export-*"))


def test_export_rejects_symlink_artifacts(tmp_path, artifacts):
    destination = tmp_path / "export"
    external = tmp_path / "outside.txt"
    external.write_text("private", encoding="utf-8")
    try:
        (artifacts["task-2"] / "link.txt").symlink_to(external)
    except OSError as error:
        pytest.skip(f"symbolic link unavailable: {error}")
    with pytest.raises(ValueError, match="symbolic links"):
        export(destination, artifacts)
    assert not destination.exists()
