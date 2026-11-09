import asyncio
import json
from types import SimpleNamespace

import pytest

from harness.cases import EvaluationSuite
from harness.metrics import EvaluationReport
from harness.reporting import write_report
from harness.runner import run_suite
from protocol.models import FailureCode, TaskStatus
from tests.harness.test_metrics import make_case, make_result


@pytest.mark.parametrize("failure", ["factory", "execution", "timeout"])
async def test_one_failure_does_not_stop_later_case(tmp_path, failure):
    suite = EvaluationSuite(suite="failure-check", cases=[make_case("bad"), make_case("good")])
    visited = []

    def factory(case, root):
        visited.append(case.task_id)
        if case.task_id == "bad" and failure == "factory":
            raise ValueError("Cannot initialize")

        async def run_graph(graph, config):
            if case.task_id == "bad":
                if failure == "timeout":
                    await asyncio.Event().wait()
                raise RuntimeError("Cannot execute")
            result = make_result(case, [TaskStatus.SUCCESS])
            result.run_id = config.run_id
            return result

        return SimpleNamespace(run_graph=run_graph)

    report = await run_suite(suite, tmp_path, service_factory=factory, case_timeout_sec=0.02)
    assert visited == ["bad", "good"]
    assert [item.status for item in report.tasks] == ["failed", "success"]
    assert report.tasks[0].raw_result.failure_code == (
        FailureCode.TIMEOUT if failure == "timeout" else FailureCode.INTERNAL_ERROR
    )
    assert report.tasks[0].unreported_task_ids == ["n0"]
    assert report.summary.goal_completion_rate == 0.5
    assert all(item.rejected_result is None for item in report.tasks)
    assert all(
        "Invalid returned records" not in warning
        for item in report.tasks for warning in item.warnings
    )


async def test_rejected_records_are_preserved_without_marking_later_valid_result(tmp_path):
    suite = EvaluationSuite(suite="invalid-result", cases=[make_case("bad"), make_case("good")])
    returned = {}

    def factory(case, root):
        async def run_graph(graph, config):
            result = make_result(case, [TaskStatus.SUCCESS])
            result.run_id = config.run_id
            if case.task_id == "bad":
                result.tasks.append(result.tasks[0].model_copy(deep=True))
            returned[case.task_id] = result.model_copy(deep=True)
            return result

        return SimpleNamespace(run_graph=run_graph)

    report = await run_suite(suite, tmp_path / "work", service_factory=factory)
    rejected, valid = report.tasks
    assert rejected.status == "failed"
    assert rejected.raw_result.failure_code == FailureCode.INTERNAL_ERROR
    assert "duplicate task records" in rejected.failure_reason
    assert rejected.raw_result.tasks == []
    assert rejected.rejected_result == returned["bad"]
    assert len(rejected.rejected_result.tasks) == 2
    assert any("Invalid returned records" in warning for warning in rejected.warnings)
    assert valid.status == "success"
    assert valid.raw_result == returned["good"]
    assert valid.rejected_result is None
    assert all("Invalid returned records" not in warning for warning in valid.warnings)
    assert (report.summary.goals_passed, report.summary.goals_total) == (1, 2)
    assert (report.summary.subtasks_passed, report.summary.subtasks_total) == (1, 1)
    path = write_report(report, tmp_path / "report.json")
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert len(saved["tasks"][0]["rejected_result"]["tasks"]) == 2
    assert saved["tasks"][1]["rejected_result"] is None


async def test_repeated_batches_have_distinct_roots_ids_and_keep_old_files(tmp_path):
    suite = EvaluationSuite(suite="repeat", cases=[make_case("first"), make_case("second")])
    roots, run_ids = [], []

    def factory(case, root):
        roots.append(root)
        (root / "marker.txt").write_text(case.task_id, encoding="utf-8")

        async def run_graph(graph, config):
            run_ids.append(config.run_id)
            result = make_result(case, [TaskStatus.SUCCESS])
            result.run_id = config.run_id
            return result

        return SimpleNamespace(run_graph=run_graph)

    first = await run_suite(suite, tmp_path, service_factory=factory)
    second = await run_suite(suite, tmp_path, service_factory=factory)
    assert first.run_id != second.run_id
    assert len(set(roots)) == len(set(run_ids)) == 4
    assert all((root / "marker.txt").is_file() for root in roots)
    assert first.suite_sha256 == second.suite_sha256


async def test_caller_cancellation_propagates_instead_of_becoming_failed_case(tmp_path):
    suite = EvaluationSuite(suite="cancel", cases=[make_case()])
    started = asyncio.Event()

    async def run_graph(graph, config):
        started.set()
        await asyncio.Event().wait()

    running = asyncio.create_task(run_suite(
        suite, tmp_path, service_factory=lambda case, root: SimpleNamespace(run_graph=run_graph),
    ))
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
    finally:
        running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running


async def test_json_roundtrip_preserves_utf8_null_and_refuses_overwrite(tmp_path):
    case = make_case()
    suite = EvaluationSuite(suite="json-check", cases=[case])

    async def run_graph(graph, config):
        result = make_result(case, [])
        result.run_id = config.run_id
        result.graph = None
        result.goal_verdict = None
        result.failure_code = FailureCode.MODEL_ERROR
        result.failure_reason = "规划失败"
        return result

    report = await run_suite(
        suite, tmp_path / "work",
        service_factory=lambda case, root: SimpleNamespace(run_graph=run_graph),
    )
    path = write_report(report, tmp_path / "reports/result.json")
    content = path.read_text(encoding="utf-8")
    assert "规划失败" in content
    assert json.loads(content)["summary"]["subtask_completion_rate"] is None
    assert EvaluationReport.model_validate_json(content) == report
    with pytest.raises(FileExistsError):
        write_report(report, path)
    assert path.read_text(encoding="utf-8") == content
