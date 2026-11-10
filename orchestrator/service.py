"""Public entry point for D's CLI and harness."""

import asyncio
from datetime import UTC, datetime

from orchestrator.planner import Planner, PlanningError
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


class CollaborationService:
    def __init__(self, planner: Planner, scheduler: Scheduler, workers: list[AgentProfile]):
        self.planner = planner
        self.scheduler = scheduler
        self.workers = workers

    async def run_goal(
        self,
        goal: str,
        config: RunConfig,
        *,
        memory_context: str = "",
        goal_acceptance: list[AcceptanceRule] | None = None,
    ) -> RunResult:
        started_at = datetime.now(UTC)
        try:
            async with asyncio.timeout(config.task_timeout_sec):
                graph = await self.planner.plan(
                    goal,
                    self.workers,
                    config,
                    memory_context=memory_context,
                    goal_acceptance=goal_acceptance,
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
