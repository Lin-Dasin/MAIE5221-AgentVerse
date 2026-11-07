"""Reject malformed fixture data before it reaches the scheduler."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from harness.cases import EvaluationSuite, FixtureStep, load_suite
from harness.offline import FixtureWorker, OfflineModel
from protocol.models import ExecutionContext, FailureCode, RunConfig


@pytest.fixture
def suite_data():
    return {
        "suite": "unit-fixture",
        "mode": "offline",
        "cases": [
            {
                "task_id": "case-one",
                "goal": "Write a fixture report",
                "graph": {
                    "tasks": [
                        {
                            "task_id": "report",
                            "title": "Write report",
                            "assignee": "writer",
                            "outputs": ["outputs/report.md"],
                            "acceptance": [{"type": "file_exists", "path": "outputs/report.md"}],
                            "max_retries": 0,
                        }
                    ],
                    "final_task_id": "report",
                    "goal_acceptance": [{"type": "file_exists", "path": "report.md"}],
                },
                "steps": {"report": [{"files": {"outputs/report.md": "中文报告。"}}]},
            }
        ],
    }


def test_load_json_with_utf8_bom_preserves_chinese(tmp_path, suite_data):
    source = tmp_path / "suite.json"
    source.write_text(json.dumps(suite_data, ensure_ascii=False), encoding="utf-8-sig")
    loaded = load_suite(source)
    assert loaded.cases[0].steps["report"][0].files["outputs/report.md"] == "中文报告。"
    assert loaded.cases[0].graph.final_task_id == "report"


def test_bundled_suite_is_valid_and_explicitly_offline():
    source = Path(__file__).resolve().parents[2] / "harness/suites/offline-smoke.json"
    suite = load_suite(source)
    assert suite.mode == "offline"
    assert len(suite.cases) == 6
    assert any(
        step.children for case in suite.cases for steps in case.steps.values() for step in steps
    )


def test_duplicate_goal_ids_are_rejected(suite_data):
    suite_data["cases"].append(suite_data["cases"][0])
    with pytest.raises(ValidationError, match="must be unique"):
        EvaluationSuite.model_validate(suite_data)


def test_invalid_acceptance_rule_is_rejected(suite_data):
    rule = suite_data["cases"][0]["graph"]["goal_acceptance"][0]
    rule["type"] = "text_contains"
    with pytest.raises(ValidationError, match="requires non-empty text"):
        EvaluationSuite.model_validate(suite_data)


@pytest.mark.parametrize("path", ["../outside.md", "/tmp/outside.md", "outputs/../outside.md"])
def test_fixture_write_paths_cannot_traverse(suite_data, path):
    suite_data["cases"][0]["steps"]["report"][0]["files"] = {path: "outside"}
    with pytest.raises(ValidationError, match="relative POSIX"):
        EvaluationSuite.model_validate(suite_data)


def test_fixture_cannot_write_inputs(suite_data):
    suite_data["cases"][0]["steps"]["report"][0]["files"] = {"inputs/other.md": "overwrite"}
    with pytest.raises(ValidationError, match="under outputs"):
        EvaluationSuite.model_validate(suite_data)


def test_unknown_fields_are_not_ignored(suite_data):
    suite_data["cases"][0]["expected_pass"] = True
    with pytest.raises(ValidationError, match="Extra inputs"):
        EvaluationSuite.model_validate(suite_data)


@pytest.mark.parametrize("kind", ["delegate", "fail"])
def test_incomplete_fixture_actions_are_rejected(kind):
    with pytest.raises(ValidationError, match="require"):
        FixtureStep(kind=kind)


def test_unknown_script_task_is_rejected(suite_data):
    suite_data["cases"][0]["steps"]["typo"] = [{"files": {}}]
    with pytest.raises(ValidationError, match="unknown task IDs"):
        EvaluationSuite.model_validate(suite_data)


def test_empty_suite_is_rejected():
    with pytest.raises(ValidationError):
        EvaluationSuite(suite="empty", cases=[])


def test_non_json_file_is_rejected(tmp_path):
    source = tmp_path / "broken.json"
    source.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_suite(source)


async def test_worker_release_does_not_restart_script_on_retry(tmp_path, suite_data):
    case = EvaluationSuite.model_validate(suite_data).cases[0]
    worker = FixtureWorker(
        tmp_path,
        {"report": [
            FixtureStep(files={"outputs/report.md": "first"}),
            FixtureStep(files={"outputs/report.md": "corrected"}),
        ]},
    )
    config = RunConfig(run_id="retry-fixture", memory_enabled=False, write_memory=False)
    task = case.graph.tasks[0]
    for attempt in (1, 2):
        outcome = await worker.execute(
            task,
            ExecutionContext(config=config, task_id="report", agent_id="writer", attempt=attempt),
        )
        assert outcome.kind == "finished" and outcome.tokens == 0
        worker.release(config.run_id, "report")
    assert (tmp_path / "workspaces/writer/outputs/report.md").read_text() == "corrected"


async def test_missing_script_fails_without_writing_success_files(tmp_path, suite_data):
    task = EvaluationSuite.model_validate(suite_data).cases[0].graph.tasks[0]
    worker = FixtureWorker(tmp_path, {})
    outcome = await worker.execute(
        task,
        ExecutionContext(
            config=RunConfig(memory_enabled=False, write_memory=False),
            task_id="report",
            agent_id="writer",
            attempt=1,
        ),
    )
    assert outcome.kind == "failed"
    assert outcome.failure_code == FailureCode.INVALID_OUTPUT
    assert not outcome.retryable
    assert not (tmp_path / "workspaces/writer/outputs/report.md").exists()


async def test_offline_model_has_no_live_provider_fallback():
    with pytest.raises(RuntimeError, match="model calls are disabled"):
        await OfflineModel().complete([], [], max_tokens=1)
