"""Compose Role A with explicitly supplied B/C/D adapters; no test fallbacks."""

from pathlib import Path

from orchestrator.planner import Planner
from orchestrator.scheduler import Scheduler
from orchestrator.service import CollaborationService
from protocol.models import AgentProfile, ToolDefinition
from protocol.ports import Approver, Memory, ModelClient, Observer, ToolExecutor, Verifier
from runtime.agent import AgentRuntime


def build_service(
    *,
    root: Path,
    model: ModelClient,
    profiles: dict[str, AgentProfile],
    tool_definitions: list[ToolDefinition],
    executor: ToolExecutor,
    verifier: Verifier,
    approver: Approver,
    planner_id: str = "planner",
    memory: Memory | None = None,
    observer: Observer | None = None,
) -> CollaborationService:
    """Build one serial service using a shared memory adapter and model client.

    B's executor and C's memory adapter must already use this same root. The
    caller owns containers, client shutdown and observer persistence. If memory
    is omitted, runs must disable both memory_enabled and write_memory.
    """
    planner_profile = profiles.get(planner_id)
    if planner_profile is None or planner_profile.role != "planner":
        raise ValueError("planner_id must name a registered planner profile")
    workers = [profile for profile in profiles.values() if profile.role == "worker"]
    if not workers:
        raise ValueError("at least one worker profile is required")
    runtime = AgentRuntime(model, executor, profiles, tool_definitions, memory=memory)
    scheduler = Scheduler(
        runtime,
        verifier,
        approver,
        root=root.resolve(),
        memory=memory,
        observer=observer,
    )
    return CollaborationService(Planner(model, planner_profile), scheduler, workers)
