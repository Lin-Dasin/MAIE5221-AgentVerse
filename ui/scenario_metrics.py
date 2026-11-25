"""Session-level automatic metrics for Mid/unknown scenario runs."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

METRICS_FILE = "metrics.json"
METRICS_MARKDOWN = "metrics.md"
STATE_FILE = "metrics-state.json"


def _load_json(path: Path, default):
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _latest_summary(task_dir: Path) -> dict | None:
    attempts = task_dir / "attempts"
    if not attempts.is_dir():
        return None
    files = list(attempts.glob("*/discussion-summary.json"))
    if not files:
        return None
    latest = max(files, key=lambda path: path.stat().st_mtime)
    return json.loads(latest.read_text(encoding="utf-8"))


def _memory_counts(summary: dict) -> tuple[int, int]:
    memory = summary.get("memory") or {}
    recalled = memory.get("recalled") or {}
    written = memory.get("written") or {}
    recall_count = sum(len(ids) for ids in recalled.values() if isinstance(ids, list))
    write_count = sum(len(ids) for ids in written.values() if isinstance(ids, list))
    return recall_count, write_count


def record_task_metrics(
    session_dir: Path, task_id: str, duration_sec: float | None = None
) -> Path:
    """Record one task duration and rebuild metrics.json/metrics.md for the session."""
    session_dir = Path(session_dir)
    scenario_data = _load_json(session_dir / "scenario.json", {})
    scenario = scenario_data.get("scenario", scenario_data)
    tasks = scenario.get("tasks") or []
    state_path = session_dir / STATE_FILE
    state = _load_json(state_path, {"durations": {}})
    state.setdefault("durations", {})
    if duration_sec is not None:
        state["durations"][task_id] = round(max(0.0, duration_sec), 3)
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    rows = []
    for task in tasks:
        task_key = task.get("task_id")
        task_dir = session_dir / "tasks" / str(task_key)
        summary = _latest_summary(task_dir) or {}
        recall_count, write_count = _memory_counts(summary)
        rows.append(
            {
                "task_id": task_key,
                "status": summary.get("status", "not_run"),
                "stop_reason": summary.get("stop_reason"),
                "duration_sec": state["durations"].get(task_key, 0.0),
                "calls": summary.get("model_calls", 0),
                "tokens": summary.get("tokens", 0),
                "rework_rounds": max(0, len(summary.get("attempts") or []) - 1),
                "memory_recall": recall_count,
                "memory_write": write_count,
                "hitl_triggers": summary.get("hitl_triggers", 0),
                "accepted": (task_dir / "accepted").is_dir(),
            }
        )

    total = len(rows)
    run = sum(1 for row in rows if row["status"] != "not_run")
    passed = sum(1 for row in rows if row["status"] == "passed")
    accepted = sum(1 for row in rows if row["accepted"])
    summary = {
        "tasks_total": total,
        "tasks_run": run,
        "tasks_passed": passed,
        "tasks_accepted": accepted,
        "task_completion_rate": (passed / total) if total else None,
        "scenario_completed": total > 0 and passed == total and accepted == total,
        "execution_time_sec": round(sum(row["duration_sec"] for row in rows), 3),
        "total_calls": sum(row["calls"] for row in rows),
        "total_tokens": sum(row["tokens"] for row in rows),
        "total_rework_rounds": sum(row["rework_rounds"] for row in rows),
        "memory_recall_events": sum(row["memory_recall"] for row in rows),
        "memory_write_events": sum(row["memory_write"] for row in rows),
        "hitl_triggers": sum(row["hitl_triggers"] for row in rows),
    }
    payload = {
        "scenario_id": scenario.get("scenario_id"),
        "conversation_id": scenario_data.get("conversation_id"),
        "generated_at": datetime.now(UTC).isoformat(),
        "tasks": rows,
        "summary": summary,
    }
    metrics_path = session_dir / METRICS_FILE
    metrics_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    lines = [
        f"# Scenario metrics — {payload['scenario_id']}",
        "",
        f"- Task completion rate: **{summary['task_completion_rate']:.3f}**"
        if summary["task_completion_rate"] is not None
        else "- Task completion rate: **undefined**",
        f"- Execution time: **{summary['execution_time_sec']:.3f} s**",
        f"- Scenario completed: **{summary['scenario_completed']}**",
        f"- Calls / tokens: {summary['total_calls']} / {summary['total_tokens']}",
        f"- Rework rounds: {summary['total_rework_rounds']}",
        f"- Memory recall / write: {summary['memory_recall_events']} / "
        f"{summary['memory_write_events']}",
        f"- HITL triggers: {summary['hitl_triggers']}",
        "",
        "| Task | Status | Stop | Duration (s) | Calls | Tokens | Rework | Recall | "
        "Write | HITL | Accepted |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['task_id']} | {row['status']} | {row['stop_reason'] or '-'} | "
            f"{row['duration_sec']:.3f} | {row['calls']} | {row['tokens']} | "
            f"{row['rework_rounds']} | {row['memory_recall']} | {row['memory_write']} | "
            f"{row['hitl_triggers']} | {row['accepted']} |"
        )
    (session_dir / METRICS_MARKDOWN).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return metrics_path


__all__ = ["METRICS_FILE", "METRICS_MARKDOWN", "record_task_metrics"]