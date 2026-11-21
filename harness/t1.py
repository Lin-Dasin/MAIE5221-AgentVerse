"""Run the fixed two-source T1 evaluation with DeepSeek and Tavily."""

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path

from bus.trace import TRACE_FILENAME, JsonlTrace, TracingToolExecutor
from harness.cases import EvaluationCase
from harness.metrics import EvaluationReport, summarize_batch, summarize_result
from harness.reporting import write_report
from harness.search_evidence import SOURCES, matched_source_urls, source_pair_defects
from harness.verdict import FileVerifier
from orchestrator.approvals import PolicyApprover
from orchestrator.bootstrap import build_service
from protocol.models import (
    AcceptanceRule,
    Contract,
    CriticResult,
    ExecutionContext,
    InputRef,
    RunConfig,
    Task,
    TaskGraph,
    ToolCall,
    ToolResult,
)
from runtime.client import DeepSeekClient
from runtime.profiles import load_profiles
from sandbox.executor import SandboxToolExecutor
from tools.registry import build

QUERIES = (
    "site:python.org/downloads/release/python-3130 Python 3.13.0 release date",
    "site:peps.python.org/pep-0703 PEP 703 build configuration flag",
)
MAX_SEARCH_RESULTS = 2
REPORT = "report.md"
GOAL_RULE = AcceptanceRule(type="file_exists", path=REPORT)
GOAL_CHECKS = 3


def make_case() -> EvaluationCase:
    research = Task(
        task_id="researcher-notes",
        title=(
            f"Call web_search once for each exact query, with max_results={MAX_SEARCH_RESULTS}: "
            f"{QUERIES[0]!r} and {QUERIES[1]!r}. Read the official Python 3.13.0 release "
            "page result for its ISO release date and the official PEP 703 result for the "
            "configure flag that disables the GIL. Write only a two-column Markdown table "
            "(URL | Fact) to outputs/sources.md, one row per source. Use bare HTTPS URLs. "
            "Each Fact cell must contain only the raw YYYY-MM-DD date or configure flag. "
            "If a source is missing, leave its row out. Finish after writing the table."
        ),
        assignee="researcher",
        outputs=["outputs/sources.md"],
        acceptance=[AcceptanceRule(type="file_exists", path="outputs/sources.md")],
        max_retries=0,
    )
    write = Task(
        task_id="writer-report",
        title="Read inputs/sources.md and copy it verbatim to outputs/report.md. Then finish.",
        assignee="writer",
        depends_on=[research.task_id],
        inputs=[
            InputRef(from_task=research.task_id, path="sources.md", destination="inputs/sources.md")
        ],
        outputs=[f"outputs/{REPORT}"],
        acceptance=[AcceptanceRule(type="file_exists", path=f"outputs/{REPORT}")],
        max_retries=0,
    )
    graph = TaskGraph(
        tasks=[research, write], final_task_id=write.task_id, goal_acceptance=[GOAL_RULE]
    )
    return EvaluationCase(
        task_id="t1-search", goal="Report two sourced Python facts", graph=graph, steps={}
    )


class SearchCall(Contract):
    query: str
    ok: bool
    provider: str | None
    matched_urls: list[str]


class SearchExecutor(SandboxToolExecutor):
    def __init__(self, root: Path) -> None:
        registry = build()
        super().__init__(
            root, {name: registry[name] for name in ("web_search", "file_write", "file_read")}
        )
        self.calls: list[SearchCall] = []
        self._started: set[str] = set()

    async def execute(self, call: ToolCall, context: ExecutionContext) -> ToolResult:
        if call.name != "web_search":
            return await super().execute(call, context)
        query = call.arguments.get("query")
        if not isinstance(query, str) or query not in QUERIES or query in self._started:
            return ToolResult(ok=False, error="T1 allows each fixed query once")
        self._started.add(query)
        bounded = call.model_copy(
            update={"arguments": {"query": query, "max_results": MAX_SEARCH_RESULTS}}
        )
        result = await super().execute(bounded, context)
        data = result.data if result.ok and isinstance(result.data, dict) else {}
        self.calls.append(
            SearchCall(
                query=query,
                ok=result.ok,
                provider=data.get("provider"),
                matched_urls=matched_source_urls(data.get("results")),
            )
        )
        return result


class SearchVerifier(FileVerifier):
    def __init__(self, executor: SearchExecutor) -> None:
        super().__init__()
        self.executor = executor

    async def check(self, rules: list[AcceptanceRule], root: Path) -> CriticResult:
        base = await super().check(rules, root)
        if rules != [GOAL_RULE]:
            return base
        defects = list(base.defects)
        observed = {
            (call.query, url)
            for call in self.executor.calls
            if call.ok and call.provider == "tavily"
            for url in call.matched_urls
        }
        expected = set(zip(QUERIES, (url for url, _ in SOURCES), strict=True))
        if not expected.issubset(observed):
            defects.append("search_evidence_missing: expected official URL for each query")
        try:
            content = (
                await asyncio.to_thread((root / REPORT).read_text, encoding="utf-8")
                if base.passed
                else ""
            )
            pairs = source_pair_defects(content)
        except (OSError, UnicodeError):
            pairs = ["expected a readable UTF-8 Markdown table"]
        if pairs:
            defects.append("source_pair_check_failed: " + "; ".join(pairs))
        return CriticResult(
            passed=not defects,
            checks_total=GOAL_CHECKS,
            checks_passed=GOAL_CHECKS - len(defects),
            defects=defects,
        )


class SearchReport(EvaluationReport):
    search_backend: str = "tavily"
    requested_model: str
    search_calls: list[SearchCall]


async def run(output: Path) -> int:
    if not os.environ.get("SEARCH_API_KEY", "").strip():
        raise ValueError("Live T1 requires SEARCH_API_KEY")
    case = make_case()
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    trace = JsonlTrace(output / TRACE_FILENAME)
    executor = SearchExecutor(output)
    profiles = load_profiles(Path(__file__).resolve().parents[1] / "profiles")
    config = RunConfig(
        memory_enabled=False,
        write_memory=False,
        max_tasks=2,
        max_agent_steps=8,
        max_task_tokens=26000,
        task_timeout_sec=90,
    )
    model = DeepSeekClient.from_environment()
    try:
        service = build_service(
            root=output,
            model=model,
            profiles=profiles,
            tool_definitions=executor.tool_definitions(),
            executor=TracingToolExecutor(executor, trace),
            verifier=SearchVerifier(executor),
            approver=PolicyApprover(),
            observer=trace,
        )
        result = await service.run_graph(case.graph, config)
        await trace.run_finished(result)
    finally:
        await model.aclose()

    report = summarize_result(case.task_id, GOAL_CHECKS, result, config, output)
    report.artifacts = [Path(path).relative_to(output).as_posix() for path in report.artifacts]
    report.trace_path = TRACE_FILENAME if trace.path.is_file() else None
    report.warnings.extend(trace.warnings)
    summary = summarize_batch([report])
    fingerprint = json.dumps(
        {"case": case.model_dump(mode="json"), "sources": SOURCES, "queries": QUERIES},
        sort_keys=True,
    )
    evaluation = SearchReport(
        run_id=result.run_id,
        suite=case.task_id,
        mode="live",
        requested_model=model.model,
        search_calls=executor.calls,
        suite_sha256=hashlib.sha256(fingerprint.encode()).hexdigest(),
        started_at=result.started_at,
        finished_at=result.finished_at,
        tasks=[report],
        summary=summary,
    )
    print(f"JSON: {write_report(evaluation, output / 'evaluation.json')}")
    print(
        f"T1 goals {summary.goals_passed}/{summary.goals_total}, "
        f"subtasks {summary.subtasks_passed}/{summary.subtasks_total}"
    )
    return int(report.status != "success" or report.trace_path is None or bool(report.warnings))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    return asyncio.run(run(args.output_dir))


if __name__ == "__main__":
    raise SystemExit(main())
