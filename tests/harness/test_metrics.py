from datetime import UTC, datetime, timedelta

import pytest

from harness.cases import EvaluationCase, FixtureStep
from harness.metrics import summarize_batch, summarize_case
from protocol.models import (
    AcceptanceRule,
    CriticResult,
    FailureCode,
    HITLDecision,
    RunConfig,
    RunResult,
    Task,
    TaskGraph,
    TaskResult,
    TaskStatus,
)


def make_case(case_id="case", node_count=1):
    nodes = [
        Task(
            task_id=f"n{i}", title="Write a report", assignee="writer",
            outputs=[f"outputs/n{i}.md"],
            acceptance=[AcceptanceRule(type="file_exists", path=f"outputs/n{i}.md")],
        )
        for i in range(node_count)
    ]
    return EvaluationCase(
        task_id=case_id, goal="Produce a report",
        graph=TaskGraph(
            tasks=nodes, final_task_id=nodes[-1].task_id,
            goal_acceptance=[AcceptanceRule(type="file_exists", path=f"n{node_count - 1}.md")],
        ),
        steps={node.task_id: [FixtureStep(files={node.outputs[0]: "done"})] for node in nodes},
    )


def make_result(case, statuses, *, goal_passed=True, duration=2):
    start = datetime(2026, 10, 30, tzinfo=UTC)
    return RunResult(
        run_id="run", graph=case.graph,
        tasks=[
            TaskResult(task_id=f"n{i}", agent_id="writer", status=status)
            for i, status in enumerate(statuses)
        ],
        started_at=start, finished_at=start + timedelta(seconds=duration),
        goal_verdict=CriticResult(
            passed=goal_passed, checks_passed=int(goal_passed), checks_total=1,
            defects=[] if goal_passed else ["Missing conclusion"],
        ),
    )


def config():
    return RunConfig(run_id="run", memory_enabled=False, write_memory=False)


def test_node_rates_are_weighted_by_node_count_and_failures_remain_in_denominator(tmp_path):
    first, second = make_case("first"), make_case("second", 3)
    reports = [
        summarize_case(first, make_result(first, [TaskStatus.SUCCESS]), config(), tmp_path),
        summarize_case(second, make_result(
            second, [TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.BLOCKED],
            goal_passed=False, duration=4,
        ), config(), tmp_path),
    ]
    summary = summarize_batch(reports)
    assert (summary.goals_passed, summary.goals_total) == (1, 2)
    assert summary.goal_completion_rate == 0.5
    assert (summary.subtasks_passed, summary.subtasks_total) == (2, 4)
    assert summary.subtask_completion_rate == 0.5
    assert summary.avg_duration_sec == 3


def test_successful_nodes_do_not_override_failed_goal_or_run_error(tmp_path):
    case = make_case()
    result = make_result(case, [TaskStatus.SUCCESS], goal_passed=False)
    report = summarize_case(case, result, config(), tmp_path)
    assert report.status == "failed" and report.subtasks_passed == 1
    assert report.acceptance_passed == 0
    result.goal_verdict = CriticResult(passed=True, checks_passed=1, checks_total=1)
    result.failure_code = FailureCode.INTERNAL_ERROR
    assert summarize_case(case, result, config(), tmp_path).status == "failed"


def test_retry_token_usage_human_decisions_and_warnings_are_preserved(tmp_path):
    case = make_case()
    result = make_result(case, [TaskStatus.SUCCESS])
    result.tasks[0].attempts = 3
    result.tasks[0].tokens = 35
    result.tasks[0].hitl_decisions = [HITLDecision(
        request_id="approval", approved=True, source="policy", assumptions=["Use English"],
    )]
    result.warnings = ["Observer failed"]
    report = summarize_case(case, result, config(), tmp_path)
    assert (report.subtasks_passed, report.subtasks_total) == (1, 1)
    assert report.tokens == 35 and report.token_scope == "worker_only"
    assert report.hitl_events == 1 and report.assumptions == ["Use English"]
    assert report.warnings == ["Observer failed"]
    assert report.raw_result.tasks[0].attempts == 3


def test_planning_failure_has_one_failed_goal_but_undefined_node_rate(tmp_path):
    case = make_case()
    result = make_result(case, [])
    result.graph = None
    result.goal_verdict = None
    result.failure_code = FailureCode.MODEL_ERROR
    report = summarize_case(case, result, config(), tmp_path)
    summary = summarize_batch([report])
    assert summary.goal_completion_rate == 0
    assert summary.goals_total == 1
    assert summary.subtasks_total == 0 and summary.subtask_completion_rate is None
    assert "subtask_completion_rate" in summary.undefined_reasons
    assert not report.acceptance_evaluated and report.unreported_task_ids == []


def test_initialization_failure_exposes_graph_nodes_missing_from_result(tmp_path):
    case = make_case(node_count=2)
    result = make_result(case, [])
    result.goal_verdict = None
    result.failure_code = FailureCode.INTERNAL_ERROR
    report = summarize_case(case, result, config(), tmp_path)
    assert report.unreported_task_ids == ["n0", "n1"]
    assert report.warnings
    assert summarize_batch([report]).unreported_subtasks_total == 2


def test_duplicate_task_records_are_rejected(tmp_path):
    case = make_case()
    result = make_result(case, [TaskStatus.SUCCESS])
    result.tasks.append(result.tasks[0].model_copy())
    with pytest.raises(ValueError, match="duplicate task records"):
        summarize_case(case, result, config(), tmp_path)


def test_empty_batch_reports_undefined_rates_instead_of_success():
    summary = summarize_batch([])
    assert summary.goals_total == summary.subtasks_total == 0
    assert summary.goal_completion_rate is None
    assert summary.subtask_completion_rate is None
    assert summary.avg_duration_sec is None
