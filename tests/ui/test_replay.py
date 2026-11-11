import json
from pathlib import Path

from ui.replay import main


def write_trace(path: Path, records: list[tuple[int, str, dict]]) -> bytes:
    body = "".join(
        json.dumps(
            {
                "schema_version": "1.0",
                "run_id": "replay-demo",
                "event_id": f"{sequence:032x}",
                "timestamp": "2026-11-10T00:00:00+00:00",
                "sequence": sequence,
                "event": event,
                **metadata,
            }
        )
        + "\n"
        for sequence, event, metadata in records
    ).encode("utf-8")
    path.write_bytes(body)
    return body


def test_replay_prints_ordered_metadata_for_a_failed_run_without_editing_trace(tmp_path, capsys):
    path = tmp_path / "trace.jsonl"
    records = [
        (1, "plan_ready", {"tasks": [{"task_id": "draft"}], "final_task_id": "draft"}),
        (
            2,
            "task_changed",
            {"task_id": "draft", "agent_id": "writer", "status": "waiting_human", "attempt": 1},
        ),
        (
            3,
            "hitl_decided",
            {
                "task_id": "draft",
                "kind": "clarification",
                "approved": True,
                "source": "human",
                "answer": "private-title",
            },
        ),
        (
            4,
            "tasks_delegated",
            {"task_id": "draft", "agent_id": "writer", "child_task_ids": ["source"]},
        ),
        (
            5,
            "artifacts_handed_off",
            {
                "task_id": "draft",
                "inputs": [
                    {"from_task": "source", "path": "facts.md", "destination": "inputs/facts.md"}
                ],
            },
        ),
        (
            6,
            "tool_finished",
            {
                "task_id": "draft",
                "agent_id": "writer",
                "tool": "file_read",
                "ok": False,
                "elapsed_sec": 0.2,
                "failure_category": "missing_file",
                "arguments": "private-arguments",
            },
        ),
        (
            7,
            "run_finished",
            {"status": "failed", "duration_sec": 1.0, "failure_code": "missing_input"},
        ),
    ]
    original = write_trace(path, records)

    assert main([str(path)]) == 0
    output = capsys.readouterr()
    assert not output.err
    assert path.read_bytes() == original
    lines = output.out.splitlines()
    assert lines[0] == "Run replay-demo | 7 events"
    assert [line.split()[2] for line in lines[1:]] == [record[1] for record in records]
    assert "waiting_human" in output.out
    assert "approved by human" in output.out
    assert "delegated to source" in output.out
    assert "source/facts.md -> inputs/facts.md" in output.out
    assert "file_read -> failed in 0.200s (missing_file)" in output.out
    assert "failed in 1.000s (missing_input)" in output.out
    assert "private-title" not in output.out and "private-arguments" not in output.out


def test_partial_trace_reports_missing_events_and_sanitizes_unknown_event(tmp_path, capsys):
    path = tmp_path / "trace.jsonl"
    write_trace(path, [(2, "future\x1b[2J", {"answer": "private"})])

    assert main([str(path)]) == 0
    output = capsys.readouterr()
    assert "future?[2J" in output.out and "Unrecognized event type" in output.out
    assert "\x1b" not in output.out and "private" not in output.out
    assert "missing sequence numbers" in output.err
    assert "no final run_finished event" in output.err


def test_corrupt_trace_returns_error_without_showing_invalid_payload(tmp_path, capsys):
    path = tmp_path / "trace.jsonl"
    path.write_text("{private-data\n", encoding="utf-8")

    assert main([str(path)]) == 2
    output = capsys.readouterr()
    assert not output.out
    assert "trace.jsonl:1:" in output.err
    assert "private-data" not in output.err
