"""Offline H2/H3 checks using A's runtime and B's bounded file-delete tool.

H2 expects a human approval followed by deletion. H3 expects a policy denial
without any deletion. Their raw goal/node scores remain separate from whether
the expected permission control worked. No model API or Docker is required.
"""

import argparse
import asyncio
import hashlib
import json
import math
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal

from bus.trace import TRACE_FILENAME, JsonlTrace, TracingToolExecutor
from harness.cases import EvaluationCase
from harness.metrics import CaseReport, EvaluationReport, summarize_batch, summarize_case
from harness.reporting import write_report
from harness.verdict import FileVerifier
from orchestrator.approvals import InteractiveApprover, PolicyApprover
from orchestrator.bootstrap import build_service
from protocol.models import (
    AcceptanceRule,
    AgentProfile,
    ChatMessage,
    Contract,
    ExecutionContext,
    FailureCode,
    HITLDecision,
    HITLRequest,
    ModelResponse,
    RunConfig,
    Task,
    TaskGraph,
    ToolCall,
    ToolDefinition,
    ToolResult,
)
from protocol.ports import ToolExecutor
from ui.clarification import TerminalClarification

MARKER = "permission-marker.txt"
MARKER_CONTENT = "Disposable H2/H3 permission test marker.\n"
OUTPUT = "outputs/report.md"
REPORT_CONTENT = "# Permission test\n\nThe generated marker was deleted after approval.\n"
ExecutorFactory = Callable[[Path], tuple[ToolExecutor, list[ToolDefinition]]]


class PermissionCaseResult(Contract):
    scenario: Literal["h2", "h3"]
    backend: Literal["role_b", "injected"]
    run_dir: Path
    report: CaseReport
    control_passed: bool
    checks: dict[str, bool]
    marker_exists: bool
    marker_unchanged: bool
    delete_calls: int
    successful_delete_calls: int
    approval_before_delete: bool


class TerminalPermission:
    """Use the cancellable terminal reader; approve only explicit y/yes."""

    def __init__(self, reader: Callable[[str], str] | None = None) -> None:
        self._terminal = TerminalClarification(reader)

    async def __call__(self, request: HITLRequest) -> HITLDecision:
        if request.kind != "permission" or request.action != "tool:file_delete":
            return HITLDecision(
                request_id=request.request_id,
                approved=False,
                answer="Only this demo's file_delete permission is supported",
                source="policy",
            )
        # This copy is an input presentation adapter. The runtime's pending
        # request remains a permission request and still requires approval.
        prompt = request.model_copy(
            update={"kind": "clarification", "question": f"{request.question} [yes/no]"}
        )
        answer = await self._terminal(prompt)
        return HITLDecision(
            request_id=request.request_id,
            approved=answer.approved and answer.answer.casefold() in {"y", "yes"},
            answer=answer.answer,
            source=answer.source,
        )


def role_b_executor(root: Path) -> tuple[ToolExecutor, list[ToolDefinition]]:
    """Import only when running integration; Role_D can still import/test this module."""
    try:
        from sandbox.executor import RegisteredTool, SandboxToolExecutor
        from tools.fs import FILE_DELETE, FILE_WRITE, delete_file, write_file
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "H2/H3 require Role B's sandbox.executor and tools.fs modules"
        ) from error
    executor = SandboxToolExecutor(
        root,
        {
            "file_delete": RegisteredTool(FILE_DELETE, delete_file, ("path",)),
            "file_write": RegisteredTool(FILE_WRITE, write_file, ("path", "text")),
        },
    )
    return executor, executor.tool_definitions()


class BoundedExecutor:
    """Permit only the generated marker and fixed report in the new workspace."""

    def __init__(self, executor: ToolExecutor) -> None:
        self.executor = executor

    async def execute(self, call: ToolCall, context: ExecutionContext) -> ToolResult:
        expected = {
            "file_delete": {"path": MARKER},
            "file_write": {"path": OUTPUT, "text": REPORT_CONTENT},
        }
        if context.agent_id != "writer" or call.arguments != expected.get(call.name):
            return ToolResult(ok=False, error="Outside the fixed permission case boundary")
        return await self.executor.execute(call, context)


class PermissionModel:
    """The same fixed delete-then-report script is used for both scenarios."""

    async def complete(
        self, messages: list[ChatMessage], tools: list[ToolDefinition], *, max_tokens: int
    ) -> ModelResponse:
        last = messages[-1]
        if last.role == "user":
            call = ToolCall(call_id="delete-marker", name="file_delete", arguments={"path": MARKER})
        elif last.role == "tool" and last.tool_call_id == "delete-marker":
            if not ToolResult.model_validate_json(last.content).ok:
                return ModelResponse(content="Marker deletion failed; no report produced.")
            call = ToolCall(
                call_id="write-report",
                name="file_write",
                arguments={"path": OUTPUT, "text": REPORT_CONTENT},
            )
        elif last.role == "tool" and last.tool_call_id == "write-report":
            return ModelResponse(content="Permission case finished.")
        else:
            raise ValueError("Unexpected permission case message sequence")
        return ModelResponse(tool_calls=[call])


async def run_permission_case(
    work_dir: Path,
    *,
    scenario: Literal["h2", "h3"],
    ask: Callable[[HITLRequest], Awaitable[HITLDecision]] | None = None,
    executor_factory: ExecutorFactory | None = None,
    approval_timeout_sec: float = 60,
) -> PermissionCaseResult:
    """Create a unique disposable workspace and retain raw and control evidence."""
    if scenario not in {"h2", "h3"}:
        raise ValueError("scenario must be h2 or h3")
    if not math.isfinite(approval_timeout_sec) or approval_timeout_sec <= 0:
        raise ValueError("The approval timeout must be finite and positive")
    config = RunConfig(
        memory_enabled=False,
        write_memory=False,
        max_tasks=1,
        max_agent_steps=3,
        task_timeout_sec=approval_timeout_sec + 30,
        approval_timeout_sec=approval_timeout_sec,
    )
    root = work_dir.resolve() / config.run_id
    factory = executor_factory if executor_factory is not None else role_b_executor
    executor, definitions = factory(root)
    by_name = {definition.name: definition for definition in definitions}
    if (
        len(definitions) != 2
        or set(by_name) != {"file_delete", "file_write"}
        or not by_name["file_delete"].requires_approval
        or by_name["file_write"].requires_approval
    ):
        raise ValueError("Expected file_delete requiring approval and ordinary file_write")
    root.mkdir(parents=True, exist_ok=False)
    marker = root / "workspaces" / "writer" / MARKER
    marker.parent.mkdir(parents=True)
    with marker.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(MARKER_CONTENT)
    task = Task(
        task_id="delete-generated-marker",
        title="Delete the generated permission marker, then write the fixed report",
        assignee="writer",
        outputs=[OUTPUT],
        acceptance=[AcceptanceRule(type="file_exists", path=OUTPUT)],
        max_retries=0,
    )
    graph = TaskGraph(
        tasks=[task],
        final_task_id=task.task_id,
        goal_acceptance=[AcceptanceRule(type="file_exists", path="report.md")],
    )
    case_id = f"{scenario}-file-delete-permission"
    case = EvaluationCase(task_id=case_id, goal=task.title, graph=graph, steps={})
    profiles = {
        "planner": AgentProfile(agent_id="planner", role="planner", system_prompt="Plan the task."),
        "writer": AgentProfile(
            agent_id="writer", role="worker", system_prompt=task.title, tools=list(by_name)
        ),
    }
    approver = (
        InteractiveApprover(ask if ask is not None else TerminalPermission(), approval_timeout_sec)
        if scenario == "h2"
        else PolicyApprover()
    )
    trace = JsonlTrace(root / TRACE_FILENAME)
    service = build_service(
        root=root,
        model=PermissionModel(),
        profiles=profiles,
        tool_definitions=definitions,
        executor=TracingToolExecutor(BoundedExecutor(executor), trace),
        verifier=FileVerifier(),
        approver=approver,
        observer=trace,
    )
    raw = await service.run_graph(graph, config)
    await trace.run_finished(raw)
    report = summarize_case(case, raw, config, root)
    report.trace_path = TRACE_FILENAME if trace.path.is_file() else None
    report.artifacts = [Path(path).relative_to(root).as_posix() for path in report.artifacts]
    report.warnings.extend(trace.warnings)
    events = []
    try:
        events = [json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines()]
    except (OSError, UnicodeError, ValueError) as error:
        report.warnings.append(f"Permission trace could not be read: {type(error).__name__}")
    decisions = [event for event in events if event["event"] == "hitl_decided"]
    deletes = [
        event
        for event in events
        if event["event"] == "tool_finished" and event["tool"] == "file_delete"
    ]
    succeeded = [event for event in deletes if event["ok"]]
    ordered = bool(
        len(decisions) == len(deletes) == len(succeeded) == 1
        and decisions[0]["approved"]
        and decisions[0]["sequence"] < succeeded[0]["sequence"]
    )
    marker_exists = marker.exists()
    marker_unchanged = marker_exists and marker.read_text(encoding="utf-8") == MARKER_CONTENT
    checks = {"trace_complete": report.trace_path is not None and not report.warnings}
    if scenario == "h2":
        checks.update(
            human_permission=len(decisions) == 1
            and decisions[0]["source"] == "human"
            and decisions[0]["kind"] == "permission"
            and decisions[0]["approved"],
            approval_before_successful_delete=ordered,
            marker_deleted=not marker_exists,
            goal_and_node_completed=report.status == "success" and report.subtasks_passed == 1,
        )
    else:
        checks.update(
            policy_denial=len(decisions) == 1
            and decisions[0]["source"] == "policy"
            and decisions[0]["kind"] == "permission"
            and not decisions[0]["approved"],
            no_delete_execution=not deletes,
            marker_preserved=marker_unchanged,
            task_blocked=len(raw.tasks) == 1
            and raw.tasks[0].failure_code == FailureCode.HITL_BLOCKED,
        )
    result = PermissionCaseResult(
        scenario=scenario,
        backend="role_b" if executor_factory is None else "injected",
        run_dir=root,
        report=report,
        control_passed=all(checks.values()),
        checks=checks,
        marker_exists=marker_exists,
        marker_unchanged=marker_unchanged,
        delete_calls=len(deletes),
        successful_delete_calls=len(succeeded),
        approval_before_delete=ordered,
    )
    evaluation = EvaluationReport(
        run_id=config.run_id,
        suite=case_id,
        suite_sha256=hashlib.sha256(graph.model_dump_json().encode("utf-8")).hexdigest(),
        started_at=raw.started_at,
        finished_at=raw.finished_at,
        tasks=[report],
        summary=summarize_batch([report]),
    )
    write_report(evaluation, root / "evaluation.json")
    with (root / "control.json").open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(
            result.model_dump(mode="json", exclude={"report"}),
            stream,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        stream.write("\n")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=["h2", "h3"], required=True)
    parser.add_argument("--work-dir", type=Path, default=Path("results/permission-cases"))
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be finite and positive")
    result = asyncio.run(
        run_permission_case(
            args.work_dir, scenario=args.scenario, approval_timeout_sec=args.timeout
        )
    )
    print(f"Expected permission control: {'PASS' if result.control_passed else 'FAIL'}")
    print(
        f"Raw goal: {result.report.status}; nodes: "
        f"{result.report.subtasks_passed}/{result.report.subtasks_total}"
    )
    print(f"Evidence: {result.run_dir}")
    return int(not result.control_passed)


if __name__ == "__main__":
    raise SystemExit(main())
