import json
from pathlib import Path

import pytest

from harness.run import main


@pytest.mark.parametrize("fail_on_goal_failure", [False, True])
def test_offline_command_writes_real_verdicts(tmp_path, capsys, fail_on_goal_failure):
    output = tmp_path / "report.json"
    args = ["--mock", "--output", str(output), "--work-dir", str(tmp_path / "runs")]
    if fail_on_goal_failure:
        args.append("--fail-on-goal-failure")
    assert main(args) == int(fail_on_goal_failure)
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["mode"] == "offline"
    assert report["summary"]["goals_passed"] == 3
    assert report["summary"]["goals_total"] == 6
    assert report["summary"]["goal_completion_rate"] == 0.5
    assert report["summary"]["subtasks_passed"] == 6
    assert report["summary"]["subtasks_total"] == 10
    assert report["summary"]["subtask_completion_rate"] == 0.6
    assert len({case["run_id"] for case in report["tasks"]}) == 6
    assert all(case["token_scope"] == "worker_only" for case in report["tasks"])
    assert all(case["duration_scope"] == "fixed_graph" for case in report["tasks"])
    for case in report["tasks"]:
        trace = Path(case["trace_path"])
        events = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
        assert all(event["run_id"] == case["run_id"] for event in events)
        assert events[-1]["event"] == "run_finished"
    assert all(case["rejected_result"] is None for case in report["tasks"])
    assert all(
        "Invalid returned records" not in warning
        for case in report["tasks"]
        for warning in case["warnings"]
    )
    successful = [case for case in report["tasks"] if case["status"] == "success"]
    assert all(case["artifacts"] for case in successful)
    assert all(Path(path).is_file() for case in successful for path in case["artifacts"])
    assert "OFFLINE" in capsys.readouterr().out


def test_existing_report_is_preserved_without_running_cases(tmp_path, capsys):
    report = tmp_path / "report.json"
    report.write_text("original evidence", encoding="utf-8")
    work = tmp_path / "runs"
    assert main(["--mock", "--output", str(report), "--work-dir", str(work)]) == 2
    assert report.read_text(encoding="utf-8") == "original evidence"
    assert not work.exists()
    assert "already exists" in capsys.readouterr().err


def test_bad_input_is_rejected_before_any_execution(tmp_path, capsys):
    suite = tmp_path / "invalid.json"
    suite.write_text('{"suite":"broken","cases":[]}', encoding="utf-8")
    work = tmp_path / "runs"
    assert main(["--mock", "--suite", str(suite), "--work-dir", str(work)]) == 2
    assert not work.exists()
    assert "Evaluation error" in capsys.readouterr().err


def test_mock_mode_must_be_explicit():
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


def test_dev_entry_point_runs_named_fixture_subset_without_mock_flag(tmp_path, capsys):
    report = tmp_path / "dev.json"
    assert (
        main(["--suite", "dev", "--output", str(report), "--work-dir", str(tmp_path / "runs")]) == 0
    )
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["suite"] == "dev" and data["mode"] == "offline"
    assert [case["task_id"] for case in data["tasks"]] == [
        "f1-status-brief",
        "f2-brief-handoff",
        "f3-two-inputs",
        "p3-delegation",
    ]
    summary = data["summary"]
    assert summary["goals_passed"] == summary["goals_total"] == 4
    assert summary["subtasks_passed"] == summary["subtasks_total"] == 8
    assert "fixture subset" in capsys.readouterr().out
