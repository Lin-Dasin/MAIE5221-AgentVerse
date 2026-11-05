"""Serial fixed-graph evaluation with one isolated directory per case."""

import asyncio
import hashlib
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from bus.trace import JsonlTrace
from harness.cases import EvaluationCase, EvaluationSuite
from harness.metrics import EvaluationReport, summarize_batch, summarize_case
from orchestrator.service import CollaborationService
from protocol.models import FailureCode, RunConfig, RunResult


@dataclass(frozen=True)
class TracedService:
    service: CollaborationService
    trace: JsonlTrace


ServiceFactory = Callable[[EvaluationCase, Path], CollaborationService | TracedService]


async def run_suite(
    suite: EvaluationSuite,
    root: Path,
    *,
    service_factory: ServiceFactory,
    config: RunConfig | None = None,
    case_timeout_sec: float = 30.0,
) -> EvaluationReport:
    """Run all cases even if one raises; cancellation still propagates to the caller."""
    if not math.isfinite(case_timeout_sec) or case_timeout_sec <= 0:
        raise ValueError("case_timeout_sec must be finite and positive")
    template = config or RunConfig(memory_enabled=False, write_memory=False)
    if template.memory_enabled or template.write_memory:
        raise ValueError("offline suites require both memory flags to be disabled")
    batch_id = uuid4().hex
    batch_root = root.resolve() / batch_id
    batch_root.mkdir(parents=True, exist_ok=False)
    started_at = datetime.now(UTC)
    reports = []
    for case in suite.cases:
        case_root = batch_root / case.task_id
        case_config = template.model_copy(update={"run_id": uuid4().hex}, deep=True)
        case_started = datetime.now(UTC)
        received = None
        trace = None
        try:
            case_root.mkdir(exist_ok=False)
            built = service_factory(case, case_root)
            if isinstance(built, TracedService):
                service, trace = built.service, built.trace
            else:
                service = built
            async with asyncio.timeout(case_timeout_sec):
                result = await service.run_graph(case.graph.model_copy(deep=True), case_config)
            received = result
            report = summarize_case(case, result, case_config, case_root)
        except Exception as exc:
            # Do not invent node verdicts if the service failed before returning records.
            result = RunResult(
                run_id=case_config.run_id,
                graph=case.graph.model_copy(deep=True),
                tasks=[],
                started_at=case_started,
                finished_at=datetime.now(UTC),
                failure_code=FailureCode.TIMEOUT
                if isinstance(exc, TimeoutError)
                else FailureCode.INTERNAL_ERROR,
                failure_reason=f"{type(exc).__name__}: {exc}",
            )
            report = summarize_case(case, result, case_config, case_root)
            if isinstance(received, RunResult):
                report.rejected_result = received
                report.warnings.append(
                    "Invalid returned records were excluded from metrics "
                    "and kept in rejected_result"
                )
        if trace is not None:
            await trace.run_finished(report.raw_result)
            if trace.path.is_file():
                report.trace_path = trace.path.as_posix()
            report.warnings.extend(trace.warnings)
        reports.append(report)
    return EvaluationReport(
        run_id=batch_id,
        suite=suite.suite,
        suite_sha256=hashlib.sha256(suite.model_dump_json().encode("utf-8")).hexdigest(),
        started_at=started_at,
        finished_at=datetime.now(UTC),
        tasks=reports,
        summary=summarize_batch(reports),
    )
