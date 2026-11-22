"""Public entry point for D's CLI and harness."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from orchestrator.planner import Planner, PlanningError
from orchestrator.planner_memory import PlannerMemoryRun
from orchestrator.preflight import PlanContractError
from orchestrator.scheduler import Scheduler
from protocol.models import (
    AcceptanceRule,
    AgentProfile,
    FailureCode,
    RunConfig,
    RunResult,
    TaskGraph,
)
from protocol.ports import Memory, MemoryObserver


class CollaborationService:
    def __init__(
        self,
        planner: Planner,
        scheduler: Scheduler,
        workers: list[AgentProfile],
        *,
        memory: Memory | None = None,
        memory_observer: MemoryObserver | None = None,
    ):
        self.planner = planner
        self.scheduler = scheduler
        self.workers = workers
        self.memory = memory
        self.memory_observer = memory_observer

    async def run_goal(
        self,
        goal: str,
        config: RunConfig,
        *,
        memory_context: str = "",
        goal_acceptance: list[AcceptanceRule] | None = None,
    ) -> RunResult:
        started_at = datetime.now(UTC)
        memory_run = None
        injection = None
        use_memory = config.memory_enabled or config.write_memory
        if self.memory is not None and goal.strip() and use_memory:
            memory_run = PlannerMemoryRun(
                goal, self.planner.profile.agent_id, config, self.memory, self.memory_observer
            )
            if config.memory_enabled and not memory_context:
                memory_context = await memory_run.retrieve()
                injection = memory_run.injected
        result = await self._run_goal(
            goal, config, started_at, goal_acceptance, memory_context, injection
        )
        if memory_run is not None:
            if config.write_memory:
                await memory_run.reflect(result, plan_validated=result.graph is not None)
            result.warnings.extend(memory_run.warnings)
            result.finished_at = datetime.now(UTC)
        return result

    async def _run_goal(
        self,
        goal: str,
        config: RunConfig,
        started_at: datetime,
        goal_acceptance: list[AcceptanceRule] | None,
        memory_context: str,
        injection: Callable[[int, str | None], Awaitable[None]] | None,
    ) -> RunResult:
        try:
            async with asyncio.timeout(config.task_timeout_sec):
                graph = await self.planner.plan(
                    goal,
                    self.workers,
                    config,
                    memory_context=memory_context,
                    goal_acceptance=goal_acceptance,
                    on_memory_call=injection,
                )
        except Exception as exc:
            return RunResult(
                run_id=config.run_id,
                tasks=[],
                started_at=started_at,
                finished_at=datetime.now(UTC),
                failure_code=FailureCode.TIMEOUT
                if isinstance(exc, TimeoutError)
                else (
                    FailureCode.INVALID_OUTPUT
                    if isinstance(exc, PlanContractError)
                    else FailureCode.MODEL_ERROR
                ),
                failure_reason="Planning failed: "
                + (
                    str(exc)
                    if isinstance(exc, (PlanningError, PlanContractError))
                    else type(exc).__name__
                ),
            )
        try:
            result = await self.scheduler.run(graph, config)
        except Exception as exc:
            return RunResult(
                run_id=config.run_id,
                graph=graph,
                tasks=[],
                started_at=started_at,
                finished_at=datetime.now(UTC),
                failure_code=FailureCode.INVALID_OUTPUT
                if isinstance(exc, PlanContractError)
                else FailureCode.INTERNAL_ERROR,
                failure_reason="Run initialization failed: "
                + (str(exc) if isinstance(exc, PlanContractError) else type(exc).__name__),
            )
        result.started_at = started_at
        return result

    async def run_graph(self, graph: TaskGraph, config: RunConfig) -> RunResult:
        """Validate and run a fixed graph; invalid contracts raise before side effects."""
        return await self.scheduler.run(graph, config)
