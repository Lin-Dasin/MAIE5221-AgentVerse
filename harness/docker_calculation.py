"""Verify that the fixed calculation is executed and archived through Docker."""

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Literal

from bus.trace import TRACE_FILENAME, JsonlTrace, TracingToolExecutor
from harness.cases import EvaluationCase, EvaluationSuite
from harness.reporting import write_report
from harness.runner import TracedService, run_suite
from harness.verdict import FileVerifier
from orchestrator.approvals import PolicyApprover
from orchestrator.bootstrap import build_service
from protocol.models import (
    AcceptanceRule,
    AgentProfile,
    Contract,
    CriticResult,
    ExecutionContext,
    RunConfig,
    Task,
    TaskGraph,
    ToolCall,
    ToolResult,
)
from protocol.ports import ModelClient
from runtime.client import DeepSeekClient
from runtime.profiles import load_profiles
from sandbox.executor import RegisteredTool, SandboxToolExecutor
from tools.registry import build

CASE_ID = "docker-calculation"
TASK_ID = "compute"
WORKER = "coder"
TOOL = "code_exec"
REPORT = "calculation.md"
MARKER = "sum_of_squares_1_to_5 = 55"
EXPECTED_MARKDOWN = f"# Docker calculation\n\n{MARKER}\n"
PROGRAM = """from pathlib import Path

total = sum(number * number for number in range(1, 6))
output = Path("outputs/calculation.md")
output.parent.mkdir(exist_ok=True)
output.write_text(
    f"# Docker calculation\\n\\nsum_of_squares_1_to_5 = {total}\\n",
    encoding="utf-8",
    newline="\\n",
)
print(total)
"""


def _case() -> EvaluationCase:
    task = Task(
        task_id=TASK_ID,
        assignee=WORKER,
        title=(
            "Call code_exec exactly once with the supplied Python program as the code argument. "
            "Use the source verbatim, without Markdown fences. Finish after the call.\n\n"
            f"{PROGRAM}"
        ),
        outputs=[f"outputs/{REPORT}"],
        max_retries=0,
        acceptance=[AcceptanceRule(type="text_contains", path=f"outputs/{REPORT}", text=MARKER)],
    )
    graph = TaskGraph(
        tasks=[task],
        final_task_id=TASK_ID,
        goal_acceptance=[AcceptanceRule(type="text_contains", path=REPORT, text=MARKER)],
    )
    return EvaluationCase(
        task_id=CASE_ID, goal="Run the fixed Docker calculation", graph=graph, steps={}
    )


class CalculationCall(Contract):
    run_id: str
    task_id: str
    worker: str
    tool: str
    executed: bool = False
    ok: bool = False
    backend: Literal["docker", "subprocess"] | None = None
    exit_code: int | None = None
    stdout_matches: bool = False


class CalculationExecutor(SandboxToolExecutor):
    def __init__(self, root: Path, tool: RegisteredTool) -> None:
        super().__init__(root, {TOOL: tool})
        self.calls: list[CalculationCall] = []

    async def execute(self, call: ToolCall, context: ExecutionContext) -> ToolResult:
        recorded = CalculationCall(
            run_id=context.config.run_id,
            task_id=context.task_id,
            worker=context.agent_id,
            tool=call.name,
        )
        self.calls.append(recorded)
        source = call.arguments.get("code")
        if (
            len(self.calls) != 1
            or call.name != TOOL
            or context.task_id != TASK_ID
            or context.agent_id != WORKER
            or not isinstance(source, str)
            or source.replace("\r\n", "\n").strip() != PROGRAM.strip()
        ):
            return ToolResult(ok=False, error="This case accepts one call with the fixed program")
        recorded.executed = True
        result = await super().execute(
            call.model_copy(update={"arguments": {"code": PROGRAM}}), context
        )
        recorded.ok = result.ok
        if result.ok and isinstance(result.data, dict):
            backend = result.data.get("backend")
            exit_code = result.data.get("exit_code")
            stdout = result.data.get("stdout")
            if backend in ("docker", "subprocess"):
                recorded.backend = backend
            if type(exit_code) is int:
                recorded.exit_code = exit_code
            recorded.stdout_matches = isinstance(stdout, str) and stdout.strip() == "55"
        return result


class CalculationVerifier(FileVerifier):
    def __init__(self, executor: CalculationExecutor) -> None:
        super().__init__()
        self.executor = executor

    async def check(self, rules: list[AcceptanceRule], root: Path) -> CriticResult:
        base = await super().check(rules, root)
        if not base.passed:
            return base
        calls = self.executor.calls
        defects = []
        if len(calls) != 1 or not (
            calls[0].executed
            and calls[0].ok
            and calls[0].backend == "docker"
            and calls[0].exit_code == 0
            and calls[0].stdout_matches
        ):
            defects.append("expected one successful Docker call with exit code 0 and stdout 55")
        try:
            content = await asyncio.to_thread((root / rules[0].path).read_bytes)
        except OSError:
            content = b""
        if content != EXPECTED_MARKDOWN.encode("utf-8"):
            defects.append("calculation.md did not match the expected UTF-8 bytes")
        return CriticResult(
            passed=not defects, checks_total=1, checks_passed=int(not defects), defects=defects
        )


async def run(
    work_dir: Path,
    *,
    model: ModelClient,
    profiles: dict[str, AgentProfile],
    tool: RegisteredTool,
    mode: Literal["offline", "live"] = "offline",
) -> tuple[Path, bool]:
    planner = profiles.get("planner")
    coder = profiles.get(WORKER)
    if planner is None or coder is None or coder.role != "worker" or TOOL not in coder.tools:
        raise ValueError(
            "The fixed case requires planner and a coder Worker that declares code_exec"
        )
    scoped_profiles = {"planner": planner, WORKER: coder.model_copy(update={"tools": [TOOL]})}
    executor: CalculationExecutor | None = None

    def service_factory(_: EvaluationCase, root: Path) -> TracedService:
        nonlocal executor
        executor = CalculationExecutor(root, tool)
        trace = JsonlTrace(root / TRACE_FILENAME)
        service = build_service(
            root=root,
            model=model,
            profiles=scoped_profiles,
            tool_definitions=executor.tool_definitions(),
            executor=TracingToolExecutor(executor, trace),
            verifier=CalculationVerifier(executor),
            approver=PolicyApprover(),
            observer=trace,
        )
        return TracedService(service, trace)

    suite = EvaluationSuite(suite=CASE_ID, cases=[_case()])
    config = RunConfig(memory_enabled=False, write_memory=False, max_tasks=1, max_agent_steps=4)
    report = await run_suite(suite, work_dir, config=config, service_factory=service_factory)
    report = report.model_copy(update={"mode": mode})
    directory = work_dir.resolve() / report.run_id
    result = report.tasks[0]
    passed = (
        result.status == "success"
        and result.subtasks_passed == result.subtasks_total == 1
        and result.trace_path is not None
        and not result.warnings
    )
    evidence = {
        "mode": mode,
        "run_id": result.run_id,
        "passed": passed,
        "calls": [call.model_dump() for call in executor.calls] if executor else [],
    }
    write_report(report, directory / "evaluation.json")
    (directory / "control.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    return directory, passed


def _checked_profiles(profiles_dir: Path) -> dict[str, AgentProfile]:
    try:
        from sandbox.health import container_available
    except ModuleNotFoundError as error:
        if error.name != "sandbox.health":
            raise
        raise RuntimeError("Role B's Docker backend from PR #29 is required") from error

    if os.environ.get("CODE_EXEC_BACKEND", "").strip().lower() != "docker":
        raise ValueError("Set CODE_EXEC_BACKEND=docker for the live case")
    profiles = load_profiles(profiles_dir)
    if WORKER not in profiles or TOOL not in profiles[WORKER].tools:
        raise ValueError("The coder Worker profile from Issue #30 must declare code_exec")
    available, detail = container_available()
    if not available:
        raise RuntimeError(detail)
    return profiles


async def _run_live(work_dir: Path, profiles: dict[str, AgentProfile]) -> tuple[Path, bool]:
    model = DeepSeekClient.from_environment()
    try:
        return await run(work_dir, model=model, profiles=profiles, tool=build()[TOOL], mode="live")
    finally:
        await model.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=Path("results/docker-calculation"))
    parser.add_argument("--profiles-dir", type=Path, default=Path("profiles"))
    args = parser.parse_args()
    try:
        profiles = _checked_profiles(args.profiles_dir)
        directory, passed = asyncio.run(_run_live(args.work_dir, profiles))
    except (RuntimeError, ValueError) as error:
        parser.exit(2, f"Docker calculation: {error}\n")
    print(f"Docker calculation: {'PASS' if passed else 'FAIL'}\nEvidence: {directory}")
    return int(not passed)


if __name__ == "__main__":
    raise SystemExit(main())
