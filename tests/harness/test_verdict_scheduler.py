"""Exercise D's real file verifier through A's serial scheduler."""

from pathlib import Path

from harness.verdict import FileVerifier
from orchestrator.approvals import PolicyApprover
from orchestrator.scheduler import Scheduler
from protocol.models import (
    AcceptanceRule,
    AgentOutcome,
    ExecutionContext,
    FailureCode,
    Task,
    TaskGraph,
    TaskStatus,
)
from tests.helpers import ScriptedWorker, config


async def test_real_verdict_retries_with_feedback_and_archives_corrected_text(tmp_path: Path):
    corrected_text = "# 结论\n通过验收。\n"
    task = Task(
        task_id="report",
        title="Write a report with a conclusion",
        assignee="writer",
        outputs=["outputs/report.md"],
        acceptance=[
            AcceptanceRule(type="file_exists", path="outputs/report.md"),
            AcceptanceRule(type="text_contains", path="outputs/report.md", text="# 结论"),
        ],
        max_retries=1,
    )
    graph = TaskGraph(
        tasks=[task],
        final_task_id="report",
        goal_acceptance=[
            AcceptanceRule(type="text_contains", path="report.md", text="# 结论"),
        ],
    )

    async def write_attempt(current_task: Task, context: ExecutionContext) -> AgentOutcome:
        output = tmp_path / "workspaces" / current_task.assignee / "outputs/report.md"
        text = "# 草稿\n内容尚未完成。\n" if context.attempt == 1 else corrected_text
        output.write_text(text, encoding="utf-8")
        return AgentOutcome(kind="finished")

    worker = ScriptedWorker(tmp_path, {"report": [write_attempt, write_attempt]})
    scheduler = Scheduler(worker, FileVerifier(), PolicyApprover(), root=tmp_path)
    result = await scheduler.run(graph, config(run_id="retry-verdict"))

    assert len(result.tasks) == 1
    task_result = result.tasks[0]
    assert task_result.status == TaskStatus.SUCCESS
    assert task_result.attempts == 2
    assert task_result.critique is not None
    assert task_result.critique.checks_passed == task_result.critique.checks_total == 2
    assert len(worker.calls) == 2
    assert not worker.calls[0][1].feedback
    assert worker.calls[1][1].feedback
    assert result.failure_code is None
    assert result.goal_verdict is not None and result.goal_verdict.passed
    archived = tmp_path / "artifacts/retry-verdict/report/report.md"
    assert archived.read_text(encoding="utf-8") == corrected_text


async def test_successful_node_does_not_override_failed_goal_acceptance(tmp_path: Path):
    task = Task(
        task_id="report",
        title="Write a report",
        assignee="writer",
        outputs=["outputs/report.md"],
        acceptance=[AcceptanceRule(type="file_exists", path="outputs/report.md")],
        max_retries=0,
    )
    graph = TaskGraph(
        tasks=[task],
        final_task_id="report",
        goal_acceptance=[
            AcceptanceRule(type="text_contains", path="report.md", text="# References"),
        ],
    )
    worker = ScriptedWorker(tmp_path)
    scheduler = Scheduler(worker, FileVerifier(), PolicyApprover(), root=tmp_path)
    result = await scheduler.run(graph, config(run_id="goal-verdict"))

    assert len(result.tasks) == 1
    assert result.tasks[0].status == TaskStatus.SUCCESS
    assert result.tasks[0].critique is not None and result.tasks[0].critique.passed
    assert result.tasks[0].attempts == 1
    assert len(worker.calls) == 1
    assert (tmp_path / "artifacts/goal-verdict/report/report.md").is_file()
    assert result.goal_verdict is not None
    assert not result.goal_verdict.passed
    assert result.goal_verdict.checks_passed == 0
    assert result.goal_verdict.checks_total == 1
    assert result.goal_verdict.defects
    assert result.failure_code == FailureCode.ACCEPTANCE_FAILED
