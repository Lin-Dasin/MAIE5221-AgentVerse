"""Automatic task/scenario metrics written by the Mid scenario driver."""

import json

from ui.scenario_metrics import record_task_metrics


def _summary(status, calls=10, tokens=1000, attempts=2, hitl=1):
    return {
        "status": status,
        "stop_reason": "accepted" if status == "passed" else "rework_limit",
        "model_calls": calls,
        "tokens": tokens,
        "attempts": [{"attempt": index} for index in range(attempts)],
        "memory": {"recalled": {"writer": ["a"]}, "written": {"writer": ["b", "c"]}},
        "hitl_triggers": hitl,
    }


def _session(tmp_path):
    session = tmp_path / "session"
    (session / "tasks/task-1/attempts/run-1").mkdir(parents=True)
    (session / "tasks/task-2/attempts/run-2").mkdir(parents=True)
    (session / "scenario.json").write_text(
        json.dumps(
            {
                "scenario": {
                    "scenario_id": "blind",
                    "title": "Blind",
                    "roles": [],
                    "tasks": [
                        {"task_id": "task-1", "text": "first"},
                        {"task_id": "task-2", "text": "second"},
                    ],
                },
                "conversation_id": "conv",
            }
        ),
        encoding="utf-8",
    )
    (session / "tasks/task-1/attempts/run-1/discussion-summary.json").write_text(
        json.dumps(_summary("passed")), encoding="utf-8"
    )
    (session / "tasks/task-1/accepted").mkdir()
    return session


def test_metrics_report_task_rate_and_per_task_durations(tmp_path):
    session = _session(tmp_path)

    record_task_metrics(session, "task-1", 12.5)
    metrics = json.loads((session / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["summary"]["tasks_total"] == 2
    assert metrics["summary"]["tasks_run"] == 1
    assert metrics["summary"]["tasks_passed"] == 1
    assert metrics["summary"]["task_completion_rate"] == 0.5
    assert metrics["summary"]["execution_time_sec"] == 12.5
    assert metrics["tasks"][0]["duration_sec"] == 12.5
    assert metrics["tasks"][0]["rework_rounds"] == 1
    assert metrics["tasks"][0]["memory_recall"] == 1
    assert metrics["tasks"][0]["memory_write"] == 2
    assert "Task completion rate" in (session / "metrics.md").read_text(encoding="utf-8")

    (session / "tasks/task-2/attempts/run-2/discussion-summary.json").write_text(
        json.dumps(_summary("passed", calls=7, tokens=700, hitl=0)), encoding="utf-8"
    )
    (session / "tasks/task-2/accepted").mkdir()
    record_task_metrics(session, "task-2", 7.5)

    metrics = json.loads((session / "metrics.json").read_text(encoding="utf-8"))
    summary = metrics["summary"]
    assert summary["task_completion_rate"] == 1.0
    assert summary["scenario_completed"] is True
    assert summary["execution_time_sec"] == 20.0
    assert summary["total_calls"] == 17
    assert summary["total_tokens"] == 1700
    assert [task["duration_sec"] for task in metrics["tasks"]] == [12.5, 7.5]