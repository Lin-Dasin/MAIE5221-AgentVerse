"""Display the recorded events in a local AgentVerse JSONL trace."""

import argparse
import sys
from pathlib import Path

from bus.replay import TraceEvent, TraceReadError, read_trace


def _label(value: object) -> str:
    if isinstance(value, str) and value:
        return "".join(character if character.isprintable() else "?" for character in value)
    if isinstance(value, float):
        return f"{value:.3f}"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return "?"


def _outcome(value: object, yes: str, no: str) -> str:
    if value is True:
        return yes
    if value is False:
        return no
    return "unknown"


def _failure(code: object) -> str:
    return f" ({_label(code)})" if isinstance(code, str) and code else ""


def describe_event(event: TraceEvent) -> str:
    data = event.metadata
    task = _label(data.get("task_id"))
    agent = _label(data.get("agent_id"))
    match event.kind:
        case "plan_ready":
            tasks = data.get("tasks")
            count = len(tasks) if isinstance(tasks, list) else "?"
            return f"{count} task(s), final task {_label(data.get('final_task_id'))}"
        case "task_changed":
            status = _label(data.get("status"))
            attempt = _label(data.get("attempt"))
            return f"{task} ({agent}) -> {status}, attempt {attempt}" + _failure(
                data.get("failure_code")
            )
        case "tool_finished":
            result = _outcome(data.get("ok"), "ok", "failed")
            elapsed = _label(data.get("elapsed_sec"))
            return f"{task} ({agent}) ran {_label(data.get('tool'))} -> {result} in {elapsed}s" + (
                _failure(data.get("failure_category"))
            )
        case "artifacts_handed_off":
            inputs = data.get("inputs")
            if not isinstance(inputs, list):
                return f"{task} received ?"
            transfers = [
                f"{_label(item.get('from_task'))}/{_label(item.get('path'))}"
                f" -> {_label(item.get('destination'))}"
                for item in inputs
                if isinstance(item, dict)
            ]
            return f"{task} received " + (", ".join(transfers) or "?")
        case "hitl_decided":
            decision = _outcome(data.get("approved"), "approved", "declined")
            return (
                f"{task} {_label(data.get('kind'))} -> {decision} by {_label(data.get('source'))}"
            )
        case "tasks_delegated":
            children = data.get("child_task_ids")
            child_labels = ", ".join(map(_label, children)) if isinstance(children, list) else "?"
            return f"{task} ({agent}) delegated to {child_labels or '?'}"
        case "run_finished":
            status = _label(data.get("status"))
            duration = _label(data.get("duration_sec"))
            return f"{status} in {duration}s" + _failure(data.get("failure_code"))
        case _:
            return "Unrecognized event type"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path, help="Path to a single run's JSONL trace")
    args = parser.parse_args(argv)
    try:
        events = read_trace(args.trace)
    except TraceReadError as error:
        print(f"Could not replay trace: {error}", file=sys.stderr)
        return 2

    print(f"Run {_label(events[0].run_id)} | {len(events)} events")
    for event in events:
        print(
            f"{event.sequence:>4}  {_label(event.timestamp)}  "
            f"{_label(event.kind):<22}  {describe_event(event)}"
        )
    if any(event.sequence != index for index, event in enumerate(events, start=1)):
        print("Warning: trace has missing sequence numbers", file=sys.stderr)
    if events[-1].kind != "run_finished":
        print("Warning: trace has no final run_finished event", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
