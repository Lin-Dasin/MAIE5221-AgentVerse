"""Offline P3: real runtime delegation, child file handoff and parent consumption.

Only model responses are scripted. A schedules and resumes both workers; B reads
and writes bounded workspace files. This checks host-side file integration, not
Docker isolation or live-model planning.
"""

import argparse
import asyncio
import hashlib
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal

from bus.trace import TRACE_FILENAME, JsonlTrace, TracingToolExecutor
from harness.cases import EvaluationCase
from harness.metrics import CaseReport, EvaluationReport, summarize_batch, summarize_case
from harness.reporting import write_report
from harness.verdict import FileVerifier
from orchestrator.approvals import PolicyApprover
from orchestrator.bootstrap import build_service
from protocol.models import (
    AcceptanceRule,
    AgentProfile,
    ChatMessage,
    Contract,
    InputRef,
    ModelResponse,
    RunConfig,
    Task,
    TaskGraph,
    ToolCall,
    ToolDefinition,
    ToolResult,
)
from protocol.ports import ToolExecutor

PARENT_ID = "compose-report"
CHILD_ID = "collect-evidence"
CHILD_OUTPUT = "outputs/evidence.md"
PARENT_OUTPUT = "outputs/report.md"
EVIDENCE_MARKER = "Evidence collected by the delegated researcher."
REPORT_HEADER = "# Delegation report\n\n"
ExecutorFactory = Callable[[Path], tuple[ToolExecutor, list[ToolDefinition]]]


class DelegationCaseResult(Contract):
    backend: Literal["role_b", "injected"]
    run_dir: Path
    report: CaseReport
    control_passed: bool
    checks: dict[str, bool]
    read_path: str | None
    read_sha256: str | None
    child_archive_sha256: str | None


def role_b_executor(root: Path) -> tuple[ToolExecutor, list[ToolDefinition]]:
    """Keep B optional when importing or unit-testing Role D on its own branch."""
    try:
        from sandbox.executor import RegisteredTool, SandboxToolExecutor
        from tools.fs import FILE_READ, FILE_WRITE, read_file, write_file
    except ModuleNotFoundError as error:
        raise RuntimeError("P3 requires Role B's sandbox.executor and tools.fs modules") from error
    executor = SandboxToolExecutor(
        root,
        {
            "file_read": RegisteredTool(FILE_READ, read_file, ("path",)),
            "file_write": RegisteredTool(FILE_WRITE, write_file, ("path", "text")),
        },
    )
    return executor, executor.tool_definitions()


class DelegationModel:
    """Follow actual delegate/read responses instead of scripting the final file."""

    def __init__(self, child: Task, evidence: str) -> None:
        self.child = child
        self.evidence = evidence
        self.input_ref: InputRef | None = None
        self.read_text: str | None = None

    async def complete(
        self, messages: list[ChatMessage], tools: list[ToolDefinition], *, max_tokens: int
    ) -> ModelResponse:
        task_id = json.loads(messages[1].content)["task"]["task_id"]
        last = messages[-1]
        if last.role == "user":
            if task_id == PARENT_ID:
                call = ToolCall(
                    call_id="delegate-research",
                    name="delegate",
                    arguments={"tasks": [self.child.model_dump(mode="json", by_alias=True)]},
                )
            elif task_id == CHILD_ID:
                call = ToolCall(
                    call_id="write-evidence",
                    name="file_write",
                    arguments={"path": CHILD_OUTPUT, "text": self.evidence},
                )
            else:
                raise ValueError("Unexpected P3 task")
        elif last.role == "tool":
            result = ToolResult.model_validate_json(last.content)
            if not result.ok:
                return ModelResponse(content="P3 tool failed; no further output produced.")
            if task_id == PARENT_ID and last.tool_call_id == "delegate-research":
                data = result.data
                if not isinstance(data, dict):
                    raise ValueError("Expected a delegate result with child inputs")
                refs = [InputRef.model_validate(item) for item in data.get("inputs", [])]
                if len(refs) != 1 or refs[0].from_task != CHILD_ID:
                    raise ValueError("Expected exactly the delegated child's input")
                self.input_ref = refs[0]
                call = ToolCall(
                    call_id="read-evidence",
                    name="file_read",
                    arguments={"path": self.input_ref.destination},
                )
            elif task_id == PARENT_ID and last.tool_call_id == "read-evidence":
                if not isinstance(result.data, dict) or not isinstance(
                    result.data.get("text"), str
                ):
                    raise ValueError("Expected UTF-8 text from file_read")
                self.read_text = result.data["text"]
                call = ToolCall(
                    call_id="write-report",
                    name="file_write",
                    arguments={"path": PARENT_OUTPUT, "text": REPORT_HEADER + self.read_text},
                )
            elif (task_id, last.tool_call_id) in {
                (CHILD_ID, "write-evidence"),
                (PARENT_ID, "write-report"),
            }:
                return ModelResponse(content="P3 task finished.")
            else:
                raise ValueError("Unexpected P3 tool response")
        else:
            raise ValueError("Unexpected P3 message sequence")
        return ModelResponse(tool_calls=[call])


def _text_if_file(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.is_file() else None


def _digest(text: str | None) -> str | None:
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text is not None else None


def _ordered_trace(events: list[dict]) -> bool:
    milestones = []
    for event in events:
        if event["event"] == "task_changed":
            milestones.append((event["task_id"], event["status"]))
        else:
            milestones.append((event.get("task_id"), event["event"]))
    expected = [
        (PARENT_ID, "tasks_delegated"),
        (PARENT_ID, "waiting_children"),
        (CHILD_ID, "success"),
        (PARENT_ID, "artifacts_handed_off"),
        (PARENT_ID, "running"),
        (PARENT_ID, "success"),
        (None, "run_finished"),
    ]
    remaining = iter(milestones)
    return all(step in remaining for step in expected)


async def run_delegation_case(
    work_dir: Path, *, executor_factory: ExecutorFactory | None = None
) -> DelegationCaseResult:
    """Run one isolated fixed case and persist raw metrics plus delegation checks."""
    config = RunConfig(memory_enabled=False, write_memory=False, max_tasks=2, max_agent_steps=4)
    root = work_dir.resolve() / config.run_id
    factory = executor_factory if executor_factory is not None else role_b_executor
    executor, definitions = factory(root)
    if (
        len(definitions) != 2
        or {item.name for item in definitions} != {"file_read", "file_write"}
        or any(item.requires_approval for item in definitions)
    ):
        raise ValueError("Expected ordinary file_read and file_write definitions only")
    root.mkdir(parents=True, exist_ok=False)
    evidence = f"{EVIDENCE_MARKER}\nRun: {config.run_id}\n"
    child = Task(
        task_id=CHILD_ID,
        title="Collect evidence for the parent report",
        assignee="researcher",
        outputs=[CHILD_OUTPUT],
        acceptance=[AcceptanceRule(type="text_contains", path=CHILD_OUTPUT, text=evidence)],
        max_retries=0,
    )
    parent = Task(
        task_id=PARENT_ID,
        title="Delegate research, read its accepted file, then compose the report",
        assignee="writer",
        outputs=[PARENT_OUTPUT],
        acceptance=[
            AcceptanceRule(type="text_contains", path=PARENT_OUTPUT, text=REPORT_HEADER),
            AcceptanceRule(type="text_contains", path=PARENT_OUTPUT, text=evidence),
        ],
        max_retries=0,
    )
    graph = TaskGraph(
        tasks=[parent],
        final_task_id=PARENT_ID,
        goal_acceptance=[
            rule.model_copy(update={"path": "report.md"}) for rule in parent.acceptance
        ],
    )
    case = EvaluationCase(task_id="p3-runtime-delegation", goal=parent.title, graph=graph, steps={})
    profiles = {
        "planner": AgentProfile(agent_id="planner", role="planner", system_prompt="Plan tasks."),
        "writer": AgentProfile(
            agent_id="writer",
            role="worker",
            system_prompt=parent.title,
            tools=["file_read", "file_write"],
        ),
        "researcher": AgentProfile(
            agent_id="researcher", role="worker", system_prompt=child.title, tools=["file_write"]
        ),
    }
    trace = JsonlTrace(root / TRACE_FILENAME)
    model = DelegationModel(child, evidence)
    service = build_service(
        root=root,
        model=model,
        profiles=profiles,
        tool_definitions=definitions,
        executor=TracingToolExecutor(executor, trace),
        verifier=FileVerifier(),
        approver=PolicyApprover(),
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
        report.warnings.append(f"Delegation trace could not be read: {type(error).__name__}")
    nodes = {task.task_id: task for task in raw.graph.tasks} if raw.graph is not None else {}
    records = {task.task_id: task for task in raw.tasks}
    archive = root / "artifacts" / config.run_id
    child_text = _text_if_file(archive / CHILD_ID / "evidence.md")
    parent_text = _text_if_file(archive / PARENT_ID / "report.md")
    read_path = model.input_ref.destination if model.input_ref is not None else None
    input_text = _text_if_file(root / "workspaces" / "writer" / read_path) if read_path else None
    handoffs = [event for event in events if event["event"] == "artifacts_handed_off"]
    tools = [event for event in events if event["event"] == "tool_finished"]
    plans = [event for event in events if event["event"] == "plan_ready"]
    checks = {
        "trace_complete": report.trace_path is not None and not report.warnings,
        "initial_parent_only": len(plans) == 1
        and [item["task_id"] for item in plans[0]["tasks"]] == [PARENT_ID],
        "dynamic_child_graph": set(nodes) == {PARENT_ID, CHILD_ID}
        and nodes[CHILD_ID].depth == 1
        and nodes[CHILD_ID].parent_task == PARENT_ID,
        "nodes_counted_once": len(raw.tasks) == len(records) == report.subtasks_total == 2
        and set(records) == {PARENT_ID, CHILD_ID}
        and not report.unreported_task_ids,
        "goal_and_nodes_completed": report.status == "success" and report.subtasks_passed == 2,
        "resume_without_retry": set(records) == {PARENT_ID, CHILD_ID}
        and all(item.attempts == 1 for item in records.values()),
        "accepted_handoff": model.input_ref is not None
        and len(handoffs) == 1
        and handoffs[0]["task_id"] == PARENT_ID
        and handoffs[0]["inputs"]
        == [{"from_task": CHILD_ID, "path": "evidence.md", "destination": read_path}],
        "ordered_delegation_trace": _ordered_trace(events),
        "file_tool_sequence": [(item["task_id"], item["tool"]) for item in tools]
        == [(CHILD_ID, "file_write"), (PARENT_ID, "file_read"), (PARENT_ID, "file_write")]
        and all(item["ok"] for item in tools),
        "read_matches_accepted_file": child_text is not None
        and child_text == input_text == model.read_text,
        "parent_uses_read_content": model.read_text is not None
        and parent_text == REPORT_HEADER + model.read_text,
    }
    result = DelegationCaseResult(
        backend="role_b" if executor_factory is None else "injected",
        run_dir=root,
        report=report,
        control_passed=all(checks.values()),
        checks=checks,
        read_path=read_path,
        read_sha256=_digest(model.read_text),
        child_archive_sha256=_digest(child_text),
    )
    fixture = {"graph": graph.model_dump(mode="json"), "child": child.model_dump(mode="json")}
    evaluation = EvaluationReport(
        run_id=config.run_id,
        suite=case.task_id,
        suite_sha256=hashlib.sha256(json.dumps(fixture, sort_keys=True).encode()).hexdigest(),
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=Path("results/delegation-case"))
    args = parser.parse_args(argv)
    result = asyncio.run(run_delegation_case(args.work_dir))
    print(f"Delegation control: {'PASS' if result.control_passed else 'FAIL'}")
    print(
        f"Raw goal: {result.report.status}; nodes: "
        f"{result.report.subtasks_passed}/{result.report.subtasks_total}"
    )
    print(f"Evidence: {result.run_dir}")
    return int(not result.control_passed)


if __name__ == "__main__":
    raise SystemExit(main())
