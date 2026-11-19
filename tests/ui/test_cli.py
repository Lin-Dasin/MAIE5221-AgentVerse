"""Exercise CLI dispatch without constructing a model client or calling an API."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from harness.cases import EvaluationCase
from harness.metrics import EvaluationReport, summarize_batch, summarize_case
from harness.reporting import write_report
from protocol.models import (
    AcceptanceRule,
    CriticResult,
    RunConfig,
    RunResult,
    Task,
    TaskGraph,
    TaskResult,
    TaskStatus,
)
from ui import cli
from ui import goal as goal_module


@pytest.fixture
def evaluation(tmp_path):
    start = datetime(2026, 11, 11, tzinfo=UTC)
    reports = []
    for index, passed in enumerate([True, False]):
        config = RunConfig(run_id=f"run-{index}", memory_enabled=False, write_memory=False)
        nodes = [
            Task(
                task_id=f"node-{i}",
                title="Write report",
                assignee="writer",
                outputs=[f"outputs/report-{i}.md"],
                acceptance=[AcceptanceRule(type="file_exists", path=f"outputs/report-{i}.md")],
            )
            for i in range(2)
        ]
        graph = TaskGraph(
            tasks=nodes,
            final_task_id=nodes[-1].task_id,
            goal_acceptance=[AcceptanceRule(type="file_exists", path="report-1.md")],
        )
        case = EvaluationCase(task_id=f"case-{index}", goal="Write reports", graph=graph, steps={})
        raw = RunResult(
            run_id=config.run_id,
            graph=graph,
            tasks=[
                TaskResult(
                    task_id=node.task_id,
                    agent_id="writer",
                    status=TaskStatus.SUCCESS if passed or i == 0 else TaskStatus.FAILED,
                )
                for i, node in enumerate(nodes)
            ],
            started_at=start,
            finished_at=start + timedelta(seconds=2 + index * 2),
            goal_verdict=CriticResult(
                passed=passed,
                checks_passed=int(passed),
                checks_total=1,
                defects=[] if passed else ["Missing final report"],
            ),
        )
        reports.append(summarize_case(case, raw, config, tmp_path))
    result = EvaluationReport(
        run_id="batch",
        suite="cli-test",
        suite_sha256="0" * 64,
        started_at=start,
        finished_at=start + timedelta(seconds=6),
        tasks=reports,
        summary=summarize_batch(reports),
    )
    path = tmp_path / "reports" / "custom-evaluation.json"
    write_report(result, path)
    return path, result


@pytest.mark.parametrize("argv", [[], ["--help"]])
def test_root_help_lists_commands_without_running_them(argv, capsys):
    if argv:
        with pytest.raises(SystemExit) as caught:
            cli.main(argv)
        assert caught.value.code == 0
    else:
        assert cli.main(argv) == 0
    output = capsys.readouterr()
    assert not output.err
    for command in ["run", "h1", "permission", "p3", "evaluate", "summary", "replay"]:
        assert command in output.out


def test_replay_help_is_forwarded_to_subcommand(capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(["replay", "--help"])
    assert caught.value.code == 0
    output = capsys.readouterr()
    assert "Path to a single run's JSONL trace" in output.out
    assert not output.err


@pytest.mark.parametrize(
    "command, module",
    [
        ("h1", cli.h1_demo),
        ("permission", cli.permission_cases),
        ("p3", cli.delegation_case),
        ("evaluate", cli.evaluation),
        ("replay", cli.replay),
    ],
)
def test_subcommand_options_and_exit_status_are_preserved(command, module, monkeypatch):
    received = []

    def dispatch(argv):
        received.append(argv)
        return 7

    monkeypatch.setattr(module, "main", dispatch)
    options = ["path with spaces", "--example-option", "example-value"]
    assert cli.main([command, *options]) == 7
    assert received == [options]


def test_summary_reads_real_evaluation_counts_without_editing_file(evaluation, capsys):
    path, _ = evaluation
    original = path.read_bytes()
    assert cli.main(["summary", str(path)]) == 0
    output = capsys.readouterr()
    assert output.out.splitlines() == ["Goals: 1/2", "Subtasks: 3/4", "Average duration: 3.00s"]
    assert not output.err
    assert path.read_bytes() == original


def test_invalid_summary_returns_readable_error(tmp_path, capsys):
    path = tmp_path / "wrong-report.json"
    path.write_text("[]", encoding="utf-8")
    assert cli.main(["summary", str(path)]) == 2
    output = capsys.readouterr()
    assert not output.out
    assert "AgentVerse error: Expected an evaluation JSON report with a summary" in output.err
    assert "Traceback" not in output.err


def test_run_parses_acceptance_and_prints_returned_report_path(
    tmp_path, evaluation, monkeypatch, capsys
):
    path, stored = evaluation
    report = stored.tasks[0].model_copy(
        update={"artifacts": ["artifacts/report.md"], "trace_path": "trace.jsonl"}
    )
    acceptance_path = tmp_path / "acceptance.json"
    acceptance_path.write_text(
        json.dumps([{"type": "file_exists", "path": "report.md"}]), encoding="utf-8"
    )
    work_dir, profiles_dir = tmp_path / "runs", tmp_path / "profiles"
    received = []

    async def fake_run_goal(goal, work_dir, *, rules, profiles_dir):
        received.append((goal, work_dir, rules, profiles_dir))
        return path, report

    monkeypatch.setattr(goal_module, "run_goal", fake_run_goal)
    assert (
        cli.main(
            [
                "run",
                "  写一份报告  ",
                "--work-dir",
                str(work_dir),
                "--profiles-dir",
                str(profiles_dir),
                "--acceptance",
                str(acceptance_path),
            ]
        )
        == 0
    )
    assert received == [
        (
            "写一份报告",
            work_dir,
            [AcceptanceRule(type="file_exists", path="report.md")],
            profiles_dir,
        )
    ]
    output = capsys.readouterr()
    assert not output.err
    assert f"Evaluation: {path}" in output.out
    assert f"Artifact: {path.parent / 'artifacts/report.md'}" in output.out
    assert f"Trace: {path.parent / 'trace.jsonl'}" in output.out
    assert "Goal: success" in output.out and "Subtasks: 2/2" in output.out


@pytest.mark.parametrize("outcome", ["failed", "warning"])
def test_run_defaults_and_unsuccessful_exit_status(outcome, evaluation, monkeypatch, capsys):
    path, stored = evaluation
    report = (
        stored.tasks[1]
        if outcome == "failed"
        else stored.tasks[0].model_copy(update={"warnings": ["Trace write failed"]})
    )

    async def fake_run_goal(goal, work_dir, *, rules, profiles_dir):
        assert goal == "Write a report"
        assert work_dir == Path("results/cli-goals")
        assert rules is profiles_dir is None
        return path, report

    monkeypatch.setattr(goal_module, "run_goal", fake_run_goal)
    assert cli.main(["run", "Write a report"]) == 1
    output = capsys.readouterr()
    if outcome == "failed":
        assert f"Reason: {report.failure_reason}" in output.out
    assert not output.err


def test_empty_acceptance_is_rejected_before_starting_goal(tmp_path, monkeypatch, capsys):
    path = tmp_path / "empty-rules.json"
    path.write_text("[]", encoding="utf-8")

    async def must_not_run(*args, **kwargs):
        pytest.fail("An invalid acceptance file must not start the goal")

    monkeypatch.setattr(goal_module, "run_goal", must_not_run)
    with pytest.raises(SystemExit) as caught:
        cli.main(["run", "Write a report", "--acceptance", str(path)])
    assert caught.value.code == 2
    assert "--acceptance must contain at least one rule" in capsys.readouterr().err
