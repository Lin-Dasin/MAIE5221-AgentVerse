"""Goal and node metrics computed from actual execution records."""

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import Field

from harness.cases import EvaluationCase
from protocol.models import Contract, RunConfig, RunResult, TaskStatus


class CaseReport(Contract):
    task_id: str
    run_id: str
    status: Literal["success", "failed"]
    duration_sec: float = Field(ge=0)
    duration_scope: Literal["fixed_graph"] = "fixed_graph"
    tokens: int = Field(ge=0)
    token_scope: Literal["worker_only"] = "worker_only"
    artifacts: list[str]
    acceptance_passed: int = Field(ge=0)
    acceptance_total: int = Field(ge=1)
    acceptance_evaluated: bool
    subtasks_total: int = Field(ge=0)
    subtasks_passed: int = Field(ge=0)
    unreported_task_ids: list[str]
    failure_reason: str | None
    hitl_events: int = Field(ge=0)
    assumptions: list[str]
    warnings: list[str]
    config: RunConfig
    raw_result: RunResult
    rejected_result: RunResult | None = None
    trace_path: str | None = None


class Summary(Contract):
    goals_total: int
    goals_passed: int
    goal_completion_rate: float | None
    subtasks_total: int
    subtasks_passed: int
    subtask_completion_rate: float | None
    avg_duration_sec: float | None
    unreported_subtasks_total: int
    undefined_reasons: dict[str, str] = Field(default_factory=dict)


class EvaluationReport(Contract):
    schema_version: str = "1.0"
    run_id: str
    suite: str
    mode: Literal["offline"] = "offline"
    suite_sha256: str
    started_at: datetime
    finished_at: datetime
    tasks: list[CaseReport]
    summary: Summary


def summarize_case(
    case: EvaluationCase, result: RunResult, config: RunConfig, root: Path
) -> CaseReport:
    """Count each returned node once; never infer success from an absent result."""
    node_ids = [task.task_id for task in result.tasks]
    if len(node_ids) != len(set(node_ids)):
        raise ValueError("RunResult contains duplicate task records")
    if result.run_id != config.run_id:
        raise ValueError("RunResult belongs to a different run")
    duration = (result.finished_at - result.started_at).total_seconds()
    if duration < 0:
        raise ValueError("RunResult has negative elapsed time")
    verdict = result.goal_verdict
    passed = (
        result.failure_code is None
        and result.failure_reason is None
        and verdict is not None
        and verdict.passed
    )
    warnings = list(result.warnings)
    unreported = (
        [node.task_id for node in result.graph.tasks if node.task_id not in node_ids]
        if result.graph is not None
        else []
    )
    if unreported:
        warnings.append(
            "Incomplete task records: graph nodes absent from the subtask denominator: "
            + ", ".join(unreported)
        )
    artifacts = []
    for node in result.tasks:
        if node.status != TaskStatus.SUCCESS:
            continue
        for output in node.outputs:
            archived = root / "artifacts" / result.run_id / node.task_id
            candidate = (archived / output.removeprefix("outputs/")).resolve()
            if candidate.is_relative_to(archived.resolve()) and candidate.is_file():
                artifacts.append(candidate.as_posix())
    decisions = [decision for node in result.tasks for decision in node.hitl_decisions]
    failure_reason = result.failure_reason
    if not passed and not failure_reason:
        failure_reason = (
            result.failure_code.value
            if result.failure_code is not None
            else "; ".join(verdict.defects) or "Goal acceptance failed"
            if verdict is not None
            else "Goal was not evaluated"
        )
    return CaseReport(
        task_id=case.task_id,
        run_id=result.run_id,
        status="success" if passed else "failed",
        duration_sec=duration,
        tokens=sum(node.tokens for node in result.tasks),
        artifacts=artifacts,
        acceptance_passed=verdict.checks_passed if verdict is not None else 0,
        acceptance_total=verdict.checks_total
        if verdict is not None
        else len(case.graph.goal_acceptance),
        acceptance_evaluated=verdict is not None,
        subtasks_total=len(result.tasks),
        subtasks_passed=sum(node.status == TaskStatus.SUCCESS for node in result.tasks),
        unreported_task_ids=unreported,
        failure_reason=failure_reason,
        hitl_events=len(decisions),
        assumptions=[item for decision in decisions for item in decision.assumptions],
        warnings=warnings,
        config=config,
        raw_result=result,
    )


def summarize_batch(tasks: list[CaseReport]) -> Summary:
    """Micro-average nodes across runs, rather than averaging per-run percentages."""
    goals_passed = sum(task.status == "success" for task in tasks)
    nodes_total = sum(task.subtasks_total for task in tasks)
    nodes_passed = sum(task.subtasks_passed for task in tasks)
    undefined = {}
    if not tasks:
        undefined["goal_completion_rate"] = "No goals submitted"
        undefined["avg_duration_sec"] = "No goals submitted"
    if not nodes_total:
        undefined["subtask_completion_rate"] = "No task records returned"
    return Summary(
        goals_total=len(tasks),
        goals_passed=goals_passed,
        goal_completion_rate=goals_passed / len(tasks) if tasks else None,
        subtasks_total=nodes_total,
        subtasks_passed=nodes_passed,
        subtask_completion_rate=nodes_passed / nodes_total if nodes_total else None,
        avg_duration_sec=sum(task.duration_sec for task in tasks) / len(tasks) if tasks else None,
        unreported_subtasks_total=sum(len(task.unreported_task_ids) for task in tasks),
        undefined_reasons=undefined,
    )
