"""Offline Worker fixtures connected to A's real scheduler and D's real verifier."""

import asyncio
from pathlib import Path

from harness.cases import EvaluationCase, FixtureStep
from harness.verdict import FileVerifier
from orchestrator.approvals import PolicyApprover
from orchestrator.planner import Planner
from orchestrator.scheduler import Scheduler
from orchestrator.service import CollaborationService
from protocol.models import (
    AgentOutcome,
    AgentProfile,
    ChatMessage,
    ExecutionContext,
    FailureCode,
    ModelResponse,
    Task,
    ToolDefinition,
    relative_path,
)


class OfflineModel:
    """No provider fallback: fixed-graph evaluation must never call a live model."""

    async def complete(
        self, messages: list[ChatMessage], tools: list[ToolDefinition], *, max_tokens: int
    ) -> ModelResponse:
        raise RuntimeError("offline fixtures require run_graph(); model calls are disabled")


class FixtureWorker:
    """Consume explicit steps across retry/resume without generating fake verdicts."""

    def __init__(self, root: Path, steps: dict[str, list[FixtureStep]]):
        self.root = root.resolve()
        self.steps = {
            task_id: tuple(step.model_copy(deep=True) for step in script)
            for task_id, script in steps.items()
        }
        self._positions: dict[tuple[str, str], int] = {}

    async def execute(self, task: Task, context: ExecutionContext) -> AgentOutcome:
        if task.task_id != context.task_id or task.assignee != context.agent_id:
            raise ValueError("fixture task and execution context must agree")
        key = (context.config.run_id, task.task_id)
        position = self._positions.get(key, 0)
        script = self.steps.get(task.task_id, ())
        if position >= len(script):
            return AgentOutcome(
                kind="failed",
                failure_code=FailureCode.INVALID_OUTPUT,
                retryable=False,
                summary=f"Missing or exhausted offline fixture for {task.task_id}",
            )
        self._positions[key] = position + 1
        step = script[position]
        if step.kind == "fail":
            return AgentOutcome(
                kind="failed",
                failure_code=step.failure_code,
                retryable=False,
                summary=step.summary or "Explicit offline fixture failure",
            )
        await asyncio.to_thread(self._write_files, task, step)
        if step.kind == "delegate":
            return AgentOutcome(
                kind="delegate",
                children=[child.model_copy(deep=True) for child in step.children],
                summary=step.summary or "Offline fixture requested delegation",
            )
        return AgentOutcome(
            kind="finished", summary=step.summary or "Offline fixture files written"
        )

    def _write_files(self, task: Task, step: FixtureStep) -> None:
        workspace = self.root / "workspaces" / task.assignee
        resolved_workspace = workspace.resolve()
        if not resolved_workspace.is_relative_to(self.root):
            raise ValueError("fixture workspace must remain inside the run root")
        for name, text in step.files.items():
            relative_path(name)
            if not name.startswith("outputs/"):
                raise ValueError("fixture files must remain under outputs/")
            target = workspace / name
            if not target.resolve().is_relative_to(resolved_workspace):
                raise ValueError("fixture output must remain inside the worker workspace")
            if target.is_symlink():
                raise ValueError("fixture output must not be a symbolic link")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")

    def release(self, run_id: str, task_id: str) -> None:
        # A calls release before retry as well as after completion. Keep the script
        # cursor so a retry consumes the next fixture. There are no open resources;
        # the factory creates a fresh worker per case and its state dies with it.
        pass


def build_offline_service(case: EvaluationCase, root: Path) -> CollaborationService:
    agent_ids = {task.assignee for task in case.graph.tasks}
    agent_ids.update(
        child.assignee
        for script in case.steps.values()
        for step in script
        for child in step.children
    )
    workers = [
        AgentProfile(agent_id=agent_id, role="worker", system_prompt="Offline data-only fixture")
        for agent_id in sorted(agent_ids)
    ]
    planner = Planner(
        OfflineModel(),
        AgentProfile(agent_id="planner", role="planner", system_prompt="Offline fixed graph only"),
    )
    scheduler = Scheduler(
        FixtureWorker(root, case.steps), FileVerifier(), PolicyApprover(), root=root
    )
    return CollaborationService(planner, scheduler, workers)
