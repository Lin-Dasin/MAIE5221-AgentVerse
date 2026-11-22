"""Observe Planner memory without adding a scheduler node or exposing private text."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from protocol.models import (
    AcceptanceRule,
    MemoryEvent,
    MemoryPhase,
    RetrievedMemory,
    RunConfig,
    RunResult,
    Task,
    TaskResult,
    TaskStatus,
)
from protocol.ports import Memory, MemoryObserver
from runtime.memory_events import context_metadata, notify_memory


class PlannerMemoryRun:
    def __init__(
        self,
        goal: str,
        agent_id: str,
        config: RunConfig,
        memory: Memory,
        observer: MemoryObserver | None,
    ):
        # Memory's facade uses Task; this record never enters the scheduled graph.
        self.task = Task(
            task_id=f"plan-{uuid4().hex}",
            assignee=agent_id,
            title=goal.strip()[:4000],
            outputs=["outputs/p.md"],
            acceptance=[AcceptanceRule(type="file_exists", path="outputs/p.md")],
        )
        self.config = config
        self.memory = memory
        self.observer = observer
        self.warnings: list[str] = []
        self.recalled: RetrievedMemory | None = None
        self.started_at = datetime.now(UTC)

    async def _observe(
        self,
        phase: MemoryPhase,
        *,
        error: str | None = None,
        model_step: int | None = None,
    ) -> None:
        details = {}
        if phase != "reflected" and self.recalled is not None:
            details.update(context_metadata(self.recalled.text))
            if phase == "retrieved":
                details["memory_ids"] = self.recalled.memory_ids
        warning = await notify_memory(
            self.observer,
            MemoryEvent(
                run_id=self.config.run_id,
                task_id=self.task.task_id,
                agent_id=self.task.assignee,
                attempt=1,
                phase=phase,
                ok=error is None,
                model_step=model_step,
                error_type=error,
                **details,
            ),
            self.config.task_timeout_sec,
        )
        if warning and warning not in self.warnings:
            self.warnings.append(warning)

    async def retrieve(self) -> str:
        try:
            async with asyncio.timeout(min(5, self.config.task_timeout_sec)):
                self.recalled = await self.memory.retrieve(self.task.assignee, self.task)
        except Exception as exc:
            kind = type(exc).__name__
            self.warnings.append(f"Planner memory retrieval failed: {kind}")
            await self._observe("retrieved", error=kind)
            return ""
        await self._observe("retrieved")
        return self.recalled.text

    async def injected(self, step: int, error: str | None) -> None:
        if self.recalled is not None:
            await self._observe("injected", error=error, model_step=step)

    async def reflect(self, result: RunResult, *, plan_validated: bool) -> None:
        passed = (
            plan_validated
            and result.failure_code is None
            and result.failure_reason is None
            and result.goal_verdict is not None
            and result.goal_verdict.passed
        )
        if passed:
            summary = "Plan validated and the goal passed its declared acceptance checks."
        elif plan_validated:
            summary = "Plan validated, but downstream execution or goal acceptance failed."
        else:
            summary = "Planning ended before a valid task graph was produced."
        # Store verified status and a normalized failure category, not prompts,
        # graph dumps, provider errors, Worker artifact text or raw diagnostics.
        reason = None if passed else summary
        if reason and result.failure_code is not None:
            reason += f" Category: {result.failure_code.value}."
        evidence = TaskResult(
            task_id=self.task.task_id,
            agent_id=self.task.assignee,
            status=TaskStatus.SUCCESS if passed else TaskStatus.FAILED,
            started_at=self.started_at,
            finished_at=datetime.now(UTC),
            attempts=1,
            summary=summary,
            failure_code=result.failure_code,
            failure_reason=reason,
        )
        error = None
        try:
            async with asyncio.timeout(min(5, self.config.task_timeout_sec)):
                await self.memory.reflect(self.task, evidence)
        except Exception as exc:
            error = type(exc).__name__
            self.warnings.append(f"Planner memory reflection failed: {error}")
        await self._observe("reflected", error=error)
