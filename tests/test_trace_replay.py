"""Exercise the trace reader with the v1.0 event envelope."""

import json

import pytest

from bus.replay import TraceEvent, TraceReadError, read_trace


def event(**changes):
    return {
        "schema_version": "1.0",
        "event_id": "event-1",
        "run_id": "run-1",
        "timestamp": "2026-11-05T23:13:28.066144+00:00",
        "sequence": 1,
        "event": "plan_ready",
        **changes,
    }


def test_read_trace_preserves_order_metadata_and_unfinished_run(tmp_path):
    path = tmp_path / "trace.jsonl"
    first = event(final_task_id="final")
    future = event(event_id="event-2", sequence=3, event="future_event", path="inputs/交接.md")
    lines = [json.dumps(first), "  ", json.dumps(future, ensure_ascii=False)]
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode("utf-8-sig"))
    assert read_trace(path) == [
        TraceEvent(
            1, first["run_id"], first["timestamp"], "plan_ready", {"final_task_id": "final"}
        ),
        TraceEvent(
            3, first["run_id"], first["timestamp"], "future_event", {"path": "inputs/交接.md"}
        ),
    ]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"schema_version": "9"}, "schema_version"),
        ({"event_id": None}, "event_id"),
        ({"run_id": " "}, "run_id"),
        ({"event": ""}, "event"),
        ({"timestamp": "bad timestamp"}, "timestamp"),
        ({"sequence": 0}, "positive integer"),
        ({"sequence": True}, "positive integer"),
        ({"sequence": 1.5}, "positive integer"),
    ],
)
def test_read_trace_rejects_invalid_event_envelope(tmp_path, changes, message):
    path = tmp_path / "trace.jsonl"
    path.write_text("\n" + json.dumps(event(**changes)), encoding="utf-8")
    with pytest.raises(TraceReadError, match=message) as caught:
        read_trace(path)
    assert str(caught.value).startswith(f"{path}:2:")


@pytest.mark.parametrize(
    ("changes", "message"),
    [({"sequence": 2}, "strictly increasing"), ({"run_id": "run-2"}, "multiple run IDs")],
)
def test_read_trace_rejects_out_of_order_or_mixed_run(tmp_path, changes, message):
    path = tmp_path / "trace.jsonl"
    first = event(sequence=2)
    second = event(**({"sequence": 3, "event_id": "event-2"} | changes))
    path.write_text(json.dumps(first) + "\n\n" + json.dumps(second), encoding="utf-8")
    with pytest.raises(TraceReadError, match=message) as caught:
        read_trace(path)
    assert str(caught.value).startswith(f"{path}:3:")


@pytest.mark.parametrize(
    ("bad_line", "message"),
    [
        (b'{"private-data":', "invalid JSON"),
        (b'{"private-data":NaN}', "invalid JSON"),
        (b'["private-data"]', "JSON object"),
        (b'"private-data-\xff"', "UTF-8"),
    ],
)
def test_read_trace_error_hides_line_contents(tmp_path, bad_line, message):
    path = tmp_path / "trace.jsonl"
    path.write_bytes(json.dumps(event()).encode() + b"\n" + bad_line)
    with pytest.raises(TraceReadError, match=message) as caught:
        read_trace(path)
    assert str(caught.value).startswith(f"{path}:2:")
    assert "private-data" not in str(caught.value)


def test_read_trace_identifies_missing_and_empty_files(tmp_path):
    path = tmp_path / "trace.jsonl"
    with pytest.raises(TraceReadError, match="could not read trace") as caught:
        read_trace(path)
    assert str(caught.value).startswith(str(path))
    path.write_text("\n \n", encoding="utf-8-sig")
    with pytest.raises(TraceReadError, match="trace is empty") as caught:
        read_trace(path)
    assert str(caught.value).startswith(str(path))
