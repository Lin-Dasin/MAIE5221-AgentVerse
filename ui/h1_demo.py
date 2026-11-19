"""Run the offline H1 clarification demo through Role A's agent loop."""

import argparse
import asyncio
import hashlib
import math
from collections.abc import Awaitable, Callable
from pathlib import Path

from bus.trace import TRACE_FILENAME, JsonlTrace, TracingToolExecutor
from harness.cases import EvaluationCase
from harness.metrics import CaseReport, EvaluationReport, summarize_batch, summarize_case
from harness.reporting import write_report
from harness.verdict import FileVerifier
from orchestrator.approvals import InteractiveApprover
from orchestrator.bootstrap import build_service
from protocol.models import (
    AcceptanceRule,
    AgentProfile,
    ChatMessage,
    CriticResult,
    ExecutionContext,
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
from ui.clarification import TerminalClarification

CASE_ID = "h1-terminal-clarification"
REPORT = "report.md"
OUTPUT = f"outputs/{REPORT}"
QUESTION_CALL = "clarify-title"
WRITE_CALL = "write-report"
WRITE_TOOL = ToolDefinition(
    name="write_demo_report",
    description="Write the fixed H1 demo report with the title chosen by the user.",
    parameters={
        "type": "object",
        "properties": {"title": {"type": "string"}},
        "required": ["title"],
        "additionalProperties": False,
    },
)


class DemoModel:
    """Ask once, then use the scheduler's actual tool response as the title."""

    async def complete(
        self, messages: list[ChatMessage], tools: list[ToolDefinition], *, max_tokens: int
    ) -> ModelResponse:
        last = messages[-1]
        if last.role == "user":
            tool = ToolCall(
                call_id=QUESTION_CALL,
                name="ask_human",
                arguments={
                    "kind": "clarification",
                    "action": "report_title",
                    "question": "这份报告要用什么标题？",
                },
            )
        elif last.role == "tool" and last.tool_call_id == QUESTION_CALL:
            response = ToolResult.model_validate_json(last.content)
            decision = HITLDecision.model_validate(response.data)
            if not response.ok or not decision.approved:
                raise ValueError("Expected the approved clarification before writing")
            tool = ToolCall(
                call_id=WRITE_CALL,
                name=WRITE_TOOL.name,
                arguments={"title": decision.answer},
            )
        elif last.role == "tool" and last.tool_call_id == WRITE_CALL:
            response = ToolResult.model_validate_json(last.content)
            return ModelResponse(
                content="Report written." if response.ok else "Report write failed."
            )
        else:
            raise ValueError("Unexpected message sequence in the H1 demo")
        return ModelResponse(tool_calls=[tool])


class DemoExecutor:
    """Write only the demo's fixed artifact inside the new Writer workspace."""

    def __init__(self, root: Path) -> None:
        self.root = root

    async def execute(self, call: ToolCall, context: ExecutionContext) -> ToolResult:
        title = call.arguments.get("title")
        if (
            call.name != WRITE_TOOL.name
            or context.agent_id != "writer"
            or not isinstance(title, str)
            or not title.strip()
            or title.splitlines() != [title]
        ):
            return ToolResult(
                ok=False, error="Expected a single non-empty title for the demo report"
            )
        path = self.root / "workspaces" / "writer" / OUTPUT
        try:
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(f"# {title}\n\n此报告使用了用户在任务执行中确认的标题。\n")
        except OSError as error:
            return ToolResult(ok=False, error=f"Demo write failed: {type(error).__name__}")
        return ToolResult(ok=True, data={"path": OUTPUT})


class TitleVerifier(FileVerifier):
    """Require the chosen title on the first line of the final archive."""

    def __init__(self) -> None:
        self.title: str | None = None

    async def check(self, rules: list[AcceptanceRule], root: Path) -> CriticResult:
        verdict = await super().check(rules, root)
        if [rule.path for rule in rules] != [REPORT] or not verdict.passed:
            return verdict
        try:
            text = await asyncio.to_thread((root / REPORT).read_text, encoding="utf-8")
        except (OSError, UnicodeError):
            defect = "The archived report must be readable UTF-8 text"
        else:
            if self.title is not None and text.split("\n", 1)[0] == f"# {self.title}":
                return verdict
            defect = "The archived report heading does not match the approved clarification"
        return CriticResult(passed=False, checks_passed=0, checks_total=1, defects=[defect])


async def run_demo(
    work_dir: Path,
    *,
    ask: Callable[[HITLRequest], Awaitable[HITLDecision]] | None = None,
    approval_timeout_sec: float = 60,
) -> CaseReport:
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
    root.mkdir(parents=True, exist_ok=False)
    task = Task(
        task_id="write-clarified-report",
        title="Clarify the report title with the user, then write the report",
        assignee="writer",
        outputs=[OUTPUT],
        acceptance=[AcceptanceRule(type="file_exists", path=OUTPUT)],
        max_retries=0,
    )
    graph = TaskGraph(
        tasks=[task],
        final_task_id=task.task_id,
        goal_acceptance=[AcceptanceRule(type="file_exists", path=REPORT)],
    )
    case = EvaluationCase(task_id=CASE_ID, goal=task.title, graph=graph, steps={})
    profiles = {
        "planner": AgentProfile(agent_id="planner", role="planner", system_prompt="Plan the task."),
        "writer": AgentProfile(
            agent_id="writer", role="worker", system_prompt=task.title, tools=[WRITE_TOOL.name]
        ),
    }
    trace = JsonlTrace(root / TRACE_FILENAME)
    verifier = TitleVerifier()
    read_decision = ask if ask is not None else TerminalClarification()

    async def collect_decision(request: HITLRequest) -> HITLDecision:
        decision = await read_decision(request)
        if decision.approved:
            verifier.title = decision.answer
        return decision

    service = build_service(
        root=root,
        model=DemoModel(),
        profiles=profiles,
        tool_definitions=[WRITE_TOOL],
        executor=TracingToolExecutor(DemoExecutor(root), trace),
        verifier=verifier,
        approver=InteractiveApprover(collect_decision, timeout_sec=approval_timeout_sec),
        observer=trace,
    )
    result = await service.run_graph(graph, config)
    await trace.run_finished(result)
    report = summarize_case(case, result, config, root)
    report.trace_path = TRACE_FILENAME if trace.path.is_file() else None
    report.artifacts = [Path(path).relative_to(root).as_posix() for path in report.artifacts]
    report.warnings.extend(trace.warnings)
    evaluation = EvaluationReport(
        run_id=config.run_id,
        suite=CASE_ID,
        suite_sha256=hashlib.sha256(graph.model_dump_json().encode("utf-8")).hexdigest(),
        started_at=result.started_at,
        finished_at=result.finished_at,
        tasks=[report],
        summary=summarize_batch([report]),
    )
    write_report(evaluation, root / "evaluation.json")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=Path("results/h1-demo"))
    parser.add_argument("--timeout", type=float, default=60, help="Seconds to wait for a title")
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be finite and positive")
    report = asyncio.run(run_demo(args.work_dir, approval_timeout_sec=args.timeout))
    root = args.work_dir.resolve() / report.run_id
    print(f"{report.status}: {report.subtasks_passed}/{report.subtasks_total} tasks accepted")
    if report.status == "failed":
        reason = report.raw_result.tasks[0].failure_reason if report.raw_result.tasks else None
        print(f"Reason: {reason or report.failure_reason}")
    print(f"Evaluation: {root / 'evaluation.json'}")
    for artifact in report.artifacts:
        print(f"Report: {root / artifact}")
    if report.trace_path:
        print(f"Trace: {root / report.trace_path}")
    return int(report.status != "success" or report.trace_path is None or bool(report.warnings))


if __name__ == "__main__":
    raise SystemExit(main())
