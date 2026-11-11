"""Assemble the CLI's DeepSeek file workflow with the shared orchestrator."""

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

from bus.trace import TRACE_FILENAME, JsonlTrace, TracingToolExecutor
from harness.metrics import CaseReport, EvaluationReport, summarize_batch, summarize_result
from harness.reporting import write_report
from harness.verdict import FileVerifier
from orchestrator.approvals import InteractiveApprover
from orchestrator.bootstrap import build_service
from protocol.models import AcceptanceRule, RunConfig, Task, TaskResult, ToolDefinition
from protocol.ports import ToolExecutor
from runtime.client import DeepSeekClient
from runtime.profiles import load_profiles
from ui.clarification import TerminalClarification

FILE_TOOLS = ("file_read", "file_write", "file_list")
ToolFactory = Callable[[Path], tuple[ToolExecutor, list[ToolDefinition]]]


class GoalReport(EvaluationReport):
    requested_model: str


class ConsoleTrace(JsonlTrace):
    async def task_changed(self, run_id: str, task: Task, result: TaskResult) -> None:
        await super().task_changed(run_id, task, result)
        print(f"{task.task_id} ({task.assignee}) -> {result.status.value}", flush=True)


def _build_file_tools(root: Path) -> tuple[ToolExecutor, list[ToolDefinition]]:
    try:
        from sandbox.executor import SandboxToolExecutor
        from tools.registry import build
    except ModuleNotFoundError as error:
        raise RuntimeError("The run command requires Role B's registered file tools") from error
    registry = build()
    executor = SandboxToolExecutor(root, {name: registry[name] for name in FILE_TOOLS})
    return executor, executor.tool_definitions()


async def run_goal(
    goal: str,
    work_dir: Path,
    *,
    rules: list[AcceptanceRule] | None = None,
    profiles_dir: Path | None = None,
    client_factory: Callable[[], DeepSeekClient] = DeepSeekClient.from_environment,
    tool_factory: ToolFactory = _build_file_tools,
) -> tuple[Path, CaseReport]:
    if not goal.strip():
        raise ValueError("goal must contain text")
    config = RunConfig(memory_enabled=False, write_memory=False)
    root = work_dir.resolve() / config.run_id
    executor, definitions = tool_factory(root)
    names = {definition.name for definition in definitions}
    profiles = {
        agent_id: profile
        for agent_id, profile in load_profiles(profiles_dir or Path("profiles")).items()
        if profile.role == "planner" or (profile.role == "worker" and set(profile.tools) <= names)
    }
    workers = sorted(agent_id for agent_id, profile in profiles.items() if profile.role == "worker")
    if not workers or "planner" not in profiles:
        raise ValueError("Provide a planner and a worker profile compatible with the file tools")

    client = client_factory()
    try:
        root.mkdir(parents=True, exist_ok=False)
        trace = ConsoleTrace(root / TRACE_FILENAME)
        service = build_service(
            root=root,
            model=client,
            profiles=profiles,
            tool_definitions=definitions,
            executor=TracingToolExecutor(executor, trace),
            verifier=FileVerifier(),
            approver=InteractiveApprover(TerminalClarification()),
            observer=trace,
        )
        print(f"Available workers: {', '.join(workers)}", flush=True)
        result = await service.run_goal(goal, config, goal_acceptance=rules)
        await trace.run_finished(result)
    finally:
        await client.aclose()

    acceptance = result.graph.goal_acceptance if result.graph is not None else rules or []
    report = summarize_result(
        "cli-goal", len(acceptance), result, config, root, duration_scope="goal_with_planning"
    )
    report.artifacts = [
        Path(artifact).relative_to(root).as_posix() for artifact in report.artifacts
    ]
    report.trace_path = TRACE_FILENAME if trace.path.is_file() else None
    report.warnings.extend(trace.warnings)
    case_spec = {"goal": goal, "rules": [rule.model_dump(mode="json") for rule in rules or []]}
    output = GoalReport(
        run_id=result.run_id,
        suite="cli-goal",
        mode="live",
        requested_model=client.model,
        suite_sha256=hashlib.sha256(json.dumps(case_spec, sort_keys=True).encode()).hexdigest(),
        started_at=result.started_at,
        finished_at=result.finished_at,
        tasks=[report],
        summary=summarize_batch([report]),
    )
    return write_report(output, root / "evaluation.json"), report
