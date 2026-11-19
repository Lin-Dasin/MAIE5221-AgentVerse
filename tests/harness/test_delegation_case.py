"""P3 exercises A's real runtime/scheduler with injected or actual B file tools."""

import hashlib
import json
from pathlib import Path

import pytest

from harness import delegation_case
from harness.delegation_case import CHILD_ID, PARENT_ID, REPORT_HEADER, run_delegation_case
from harness.metrics import EvaluationReport
from protocol.models import FailureCode, ToolDefinition, ToolResult


@pytest.fixture
def backend():
    calls = []

    def factory(root):
        class FileExecutor:
            async def execute(self, call, context):
                calls.append((context.task_id, call))
                workspace = root / "workspaces" / context.agent_id
                path = workspace / call.arguments["path"]
                assert path.resolve().is_relative_to(workspace.resolve())
                if call.name == "file_write":
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(call.arguments["text"], encoding="utf-8")
                    return ToolResult(ok=True, data={"path": call.arguments["path"]})
                assert call.name == "file_read"
                return ToolResult(
                    ok=True,
                    data={"path": call.arguments["path"], "text": path.read_text(encoding="utf-8")},
                )

        definitions = [
            ToolDefinition(name=name, description=name, parameters={"type": "object"})
            for name in ["file_read", "file_write"]
        ]
        return FileExecutor(), definitions

    return factory, calls


def evaluation(result):
    return EvaluationReport.model_validate_json(
        (result.run_dir / "evaluation.json").read_text(encoding="utf-8")
    )


async def test_dynamic_child_is_read_and_counted_once(tmp_path: Path, backend):
    factory, calls = backend
    extra = "Backend-only evidence: 中文补充\n"

    def extended_factory(root):
        executor, definitions = factory(root)

        class ExtendedEvidence:
            async def execute(self, call, context):
                if context.task_id == CHILD_ID and call.name == "file_write":
                    call = call.model_copy(
                        update={
                            "arguments": {**call.arguments, "text": call.arguments["text"] + extra}
                        }
                    )
                return await executor.execute(call, context)

        return ExtendedEvidence(), definitions

    result = await run_delegation_case(tmp_path, executor_factory=extended_factory)
    assert result.control_passed and all(result.checks.values())
    assert result.backend == "injected"
    assert [(task_id, call.name) for task_id, call in calls] == [
        (CHILD_ID, "file_write"),
        (PARENT_ID, "file_read"),
        (PARENT_ID, "file_write"),
    ]
    expected_path = f"inputs/delegated/{CHILD_ID}/evidence.md"
    assert calls[1][1].arguments == {"path": expected_path}
    assert result.read_path == expected_path
    archive = result.run_dir / "artifacts" / result.report.run_id
    child_text = (archive / CHILD_ID / "evidence.md").read_text(encoding="utf-8")
    parent_text = (archive / PARENT_ID / "report.md").read_text(encoding="utf-8")
    assert child_text.endswith(extra)
    assert parent_text == REPORT_HEADER + child_text
    assert (
        result.read_sha256
        == result.child_archive_sha256
        == hashlib.sha256(child_text.encode("utf-8")).hexdigest()
    )
    assert result.report.tokens == 0
    assert not result.report.config.memory_enabled and not result.report.config.write_memory
    assert result.report.hitl_events == 0
    assert len(result.report.artifacts) == 2
    assert all(not Path(path).is_absolute() for path in result.report.artifacts)
    assert result.report.trace_path == "trace.jsonl"
    records = result.report.raw_result.tasks
    assert len(records) == 2 and {record.task_id for record in records} == {PARENT_ID, CHILD_ID}
    assert all(record.attempts == 1 for record in records)
    nodes = {node.task_id: node for node in result.report.raw_result.graph.tasks}
    assert nodes[CHILD_ID].depth == 1 and nodes[CHILD_ID].parent_task == PARENT_ID
    assert nodes[PARENT_ID].depth == 0
    saved = evaluation(result)
    assert saved.tasks == [result.report]
    assert saved.summary.goals_total == saved.summary.goals_passed == 1
    assert saved.summary.subtasks_total == saved.summary.subtasks_passed == 2
    assert saved.summary.goal_completion_rate == saved.summary.subtask_completion_rate == 1
    trace_text = (result.run_dir / "trace.jsonl").read_text(encoding="utf-8")
    events = [json.loads(line) for line in trace_text.splitlines()]
    assert extra.strip() not in trace_text
    assert [item["task_id"] for item in events[0]["tasks"]] == [PARENT_ID]
    delegated = [event for event in events if event["event"] == "tasks_delegated"]
    assert len(delegated) == 1 and delegated[0]["child_task_ids"] == [CHILD_ID]
    assert [
        event["status"]
        for event in events
        if event["event"] == "task_changed" and event["task_id"] == PARENT_ID
    ] == ["running", "waiting_children", "running", "reviewing", "success"]
    assert events[-1]["event"] == "run_finished" and events[-1]["status"] == "success"
    control = json.loads((result.run_dir / "control.json").read_text(encoding="utf-8"))
    assert control["control_passed"] and control["read_sha256"] == result.read_sha256
    assert "report" not in control


async def test_failed_child_prevents_parent_read_and_final_output(tmp_path: Path, backend):
    factory, calls = backend

    def missing_file_factory(root):
        _, definitions = factory(root)

        class MissingFile:
            async def execute(self, call, context):
                calls.append((context.task_id, call))
                return ToolResult(ok=True, data={"path": call.arguments["path"]})

        return MissingFile(), definitions

    result = await run_delegation_case(tmp_path, executor_factory=missing_file_factory)
    assert not result.control_passed and result.report.status == "failed"
    assert result.report.subtasks_total == 2 and result.report.subtasks_passed == 0
    records = {record.task_id: record for record in result.report.raw_result.tasks}
    assert records[CHILD_ID].failure_code == FailureCode.ACCEPTANCE_FAILED
    assert records[PARENT_ID].failure_code == FailureCode.DEPENDENCY_FAILED
    assert [(task_id, call.name) for task_id, call in calls] == [(CHILD_ID, "file_write")]
    assert not result.report.artifacts and not list(result.run_dir.rglob("report.md"))
    assert result.read_path is None and result.read_sha256 is None
    assert evaluation(result).summary.goal_completion_rate == 0


@pytest.mark.parametrize("read_behavior", ["error", "misleading"])
async def test_failed_or_inaccurate_read_cannot_pass_control(
    tmp_path: Path, backend, read_behavior
):
    factory, _ = backend

    def bad_read_factory(root):
        executor, definitions = factory(root)

        class BadRead:
            async def execute(self, call, context):
                result = await executor.execute(call, context)
                if call.name == "file_read":
                    if read_behavior == "error":
                        return ToolResult(ok=False, error="File not found: fixture")
                    return ToolResult(ok=True, data={"text": result.data["text"] + "Invented.\n"})
                return result

        return BadRead(), definitions

    result = await run_delegation_case(tmp_path, executor_factory=bad_read_factory)
    assert not result.control_passed
    assert not result.checks["read_matches_accepted_file"]
    if read_behavior == "error":
        assert result.report.status == "failed" and result.report.subtasks_passed == 1
        assert not result.checks["parent_uses_read_content"]
        assert result.read_sha256 is None
        assert not list(result.run_dir.rglob("report.md"))
    else:
        # Raw file acceptance can pass even when the tool lied about file contents.
        assert result.report.status == "success" and result.report.subtasks_passed == 2
        assert result.read_sha256 != result.child_archive_sha256
        assert result.checks["parent_uses_read_content"]
    assert result.report.subtasks_total == 2


async def test_repeated_runs_retain_separate_evidence(tmp_path: Path, backend):
    factory, _ = backend
    first = await run_delegation_case(tmp_path, executor_factory=factory)
    first_json = (first.run_dir / "evaluation.json").read_bytes()
    second = await run_delegation_case(tmp_path, executor_factory=factory)
    assert first.control_passed and second.control_passed
    assert first.run_dir != second.run_dir
    assert first.run_dir.parent == second.run_dir.parent == tmp_path.resolve()
    assert (first.run_dir / "evaluation.json").read_bytes() == first_json
    assert first.read_sha256 != second.read_sha256


async def test_invalid_tool_set_fails_before_creating_run(tmp_path: Path, backend):
    factory, calls = backend

    def invalid_factory(root):
        executor, definitions = factory(root)
        definitions[0].requires_approval = True
        return executor, definitions

    with pytest.raises(ValueError, match="ordinary file_read and file_write"):
        await run_delegation_case(tmp_path, executor_factory=invalid_factory)
    assert not list(tmp_path.iterdir()) and not calls


@pytest.mark.parametrize("success", [True, False])
def test_cli_accepts_argv_and_returns_control_exit_code(
    tmp_path: Path, backend, monkeypatch, capsys, success
):
    factory, _ = backend

    def cli_factory(root):
        executor, definitions = factory(root)

        class CliExecutor:
            async def execute(self, call, context):
                if not success:
                    return ToolResult(ok=False, error="Fixture failure")
                return await executor.execute(call, context)

        return CliExecutor(), definitions

    monkeypatch.setattr(delegation_case, "role_b_executor", cli_factory)
    assert delegation_case.main(["--work-dir", str(tmp_path)]) == int(not success)
    output = capsys.readouterr().out
    assert f"Delegation control: {'PASS' if success else 'FAIL'}" in output
    assert f"Raw goal: {'success' if success else 'failed'}" in output
    assert len(list(tmp_path.glob("*/evaluation.json"))) == 1


async def test_real_role_b_file_handoff_when_available(tmp_path: Path):
    pytest.importorskip("tools.fs", reason="Role B integration checkout is optional on Role_D")
    pytest.importorskip("sandbox.executor")
    result = await run_delegation_case(tmp_path)
    assert result.backend == "role_b" and result.control_passed
    assert result.report.status == "success" and result.report.subtasks_passed == 2
    assert result.read_sha256 == result.child_archive_sha256
