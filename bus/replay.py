"""Read the metadata events in a saved JSONL trace."""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


class TraceReadError(ValueError):
    """A trace cannot be read or does not match the supported format."""


@dataclass(frozen=True)
class TraceEvent:
    sequence: int
    run_id: str
    timestamp: str
    kind: str
    metadata: dict[str, object]


_ENVELOPE = {"schema_version", "event_id", "run_id", "timestamp", "sequence", "event"}


def _invalid_constant(_: str) -> None:
    raise ValueError("non-JSON number")


def _parse_event(line: str, location: str) -> TraceEvent:
    try:
        record = json.loads(line, parse_constant=_invalid_constant)
    except (ValueError, RecursionError):
        raise TraceReadError(f"{location}: invalid JSON") from None
    if not isinstance(record, dict):
        raise TraceReadError(f"{location}: trace event must be a JSON object")
    if record.get("schema_version") != "1.0":
        raise TraceReadError(f"{location}: expected trace schema_version 1.0")
    for field in ("event_id", "run_id", "timestamp", "event"):
        value = record.get(field)
        if not isinstance(value, str) or not value.strip():
            raise TraceReadError(f"{location}: {field} must be a non-empty string")
    sequence = record.get("sequence")
    if type(sequence) is not int or sequence <= 0:
        raise TraceReadError(f"{location}: sequence must be a positive integer")
    timestamp = record["timestamp"]
    try:
        datetime.fromisoformat(timestamp)
    except ValueError:
        raise TraceReadError(f"{location}: timestamp must be ISO 8601") from None
    return TraceEvent(
        sequence=sequence,
        run_id=record["run_id"],
        timestamp=timestamp,
        kind=record["event"],
        metadata={key: value for key, value in record.items() if key not in _ENVELOPE},
    )


def read_trace(path: Path) -> list[TraceEvent]:
    """Load a single run in file order, including a valid unfinished trace."""
    events: list[TraceEvent] = []
    try:
        with path.open("rb") as source:
            for line_number, raw_line in enumerate(source, start=1):
                location = f"{path}:{line_number}"
                encoding = "utf-8-sig" if line_number == 1 else "utf-8"
                try:
                    line = raw_line.decode(encoding).strip()
                except UnicodeDecodeError:
                    raise TraceReadError(f"{location}: trace must be UTF-8") from None
                if not line:
                    continue
                event = _parse_event(line, location)
                if events and event.run_id != events[0].run_id:
                    raise TraceReadError(f"{location}: trace contains multiple run IDs")
                if events and event.sequence <= events[-1].sequence:
                    raise TraceReadError(f"{location}: sequence must be strictly increasing")
                events.append(event)
    except OSError as exc:
        raise TraceReadError(f"{path}: could not read trace ({type(exc).__name__})") from None
    if not events:
        raise TraceReadError(f"{path}: trace is empty")
    return events
