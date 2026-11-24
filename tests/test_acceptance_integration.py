"""Role A's composition and verdict propagation using B/D's trusted pytest runner."""

from pathlib import Path

import pytest

from harness.verdict import FileVerifier
from orchestrator.approvals import PolicyApprover
from orchestrator.bootstrap import build_service
from protocol.models import AcceptanceRule, FailureCode, ModelResponse, TaskStatus, ToolCall
from sandbox.executor import SandboxToolExecutor
from sandbox.test_runner import run_pytest
from tests.helpers import ScriptedModel, config, graph, profile, task
from tools.registry import build

TEST = "from solution import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"
GOOD = "def add(a, b):\n    return a + b\n"
BAD = "def add(a, b):\n    return a - b\n"


def write_code(code):
    return ModelResponse(
        tool_calls=[
            ToolCall(
                call_id="code",
                name="file_write",
                arguments={
                    "path": "outputs/solution.py",
                    "text": code,
                },
            ),
            ToolCall(
                call_id="test",
                name="file_write",
                arguments={
                    "path": "outputs/test_solution.py",
                    "text": TEST,
                },
            ),
        ]
    )


def code_graph(*, retries=0, node_pytest=True):
    node = task("implement", max_retries=retries)
    node.outputs = ["outputs/solution.py", "outputs/test_solution.py"]
    node.acceptance = [
        AcceptanceRule(
            type="pytest_passes" if node_pytest else "file_exists",
            path="outputs/test_solution.py",
        )
    ]
    plan = graph(node)
    plan.goal_acceptance = [AcceptanceRule(type="pytest_passes", path="test_solution.py")]
    return plan


def service(root, model, verifier, *, advertise=True):
    registry = build()
    executor = SandboxToolExecutor(root, {"file_write": registry["file_write"]})
    kwargs = (
        {"supported_acceptance_types": verifier.supported_acceptance_types} if advertise else {}
    )
    return build_service(
        root=root,
        model=model,
        profiles={
            "planner": profile("planner", role="planner"),
            "writer": profile(tools=["file_write"]),
        },
        executor=executor,
        tool_definitions=executor.tool_definitions(),
        verifier=verifier,
        approver=PolicyApprover(),
        **kwargs,
    )


async def test_real_pytest_failure_feedback_retries_one_logical_node(tmp_path):
    calls = []

    def runner(root: Path, target: str):
        verdict = run_pytest(root, target, timeout_sec=10)
        calls.append((root, target, verdict["passed"]))
        return verdict

    model = ScriptedModel(write_code(BAD), "First attempt", write_code(GOOD), "Corrected")
    app = service(tmp_path, model, FileVerifier(pytest_runner=runner))
    result = await app.run_graph(code_graph(retries=1), config(run_id="code-retry"))
    assert result.goal_verdict.passed and result.failure_code is None
    assert len(result.tasks) == 1 and result.tasks[0].attempts == 2
    assert [c[2] for c in calls] == [False, True, True]
    assert [c[1] for c in calls] == [
        "outputs/test_solution.py",
        "outputs/test_solution.py",
        "test_solution.py",
    ]
    assert calls[-1][0] == tmp_path / "artifacts/code-retry/implement"
    assert "pytest_failed" in model.calls[2][0][1].content
    assert result.tasks[0].critique.passed
    assert (calls[-1][0] / "solution.py").read_text() == GOOD
    assert app.scheduler.worker.sessions == {}


async def test_failed_pytest_blocks_dependents_and_never_publishes(tmp_path):
    plan = code_graph()
    final = task("final", depends_on=["implement"])
    plan.tasks.append(final)
    plan.final_task_id = "final"
    plan.goal_acceptance = [AcceptanceRule(type="file_exists", path="final.md")]
    model = ScriptedModel(write_code(BAD), "Done")
    result = await service(tmp_path, model, FileVerifier(pytest_runner=run_pytest)).run_graph(
        plan,
        config(run_id="code-failure"),
    )
    assert [r.status for r in result.tasks] == [TaskStatus.FAILED, TaskStatus.BLOCKED]
    assert [r.failure_code for r in result.tasks] == [
        FailureCode.ACCEPTANCE_FAILED,
        FailureCode.DEPENDENCY_FAILED,
    ]
    assert result.goal_verdict is None and len(model.calls) == 2
    assert not (tmp_path / "artifacts/code-failure/implement").exists()


async def test_archive_pytest_failure_does_not_erase_node_success(tmp_path):
    result = await service(
        tmp_path,
        ScriptedModel(write_code(BAD), "Done"),
        FileVerifier(pytest_runner=run_pytest),
    ).run_graph(code_graph(node_pytest=False), config())
    assert result.tasks[0].status == TaskStatus.SUCCESS
    assert not result.goal_verdict.passed
    assert result.failure_code == FailureCode.ACCEPTANCE_FAILED
    assert "pytest_failed" in result.goal_verdict.defects[0]


@pytest.mark.parametrize("configured_runner,advertise", [(False, True), (True, False)])
async def test_pytest_requires_explicit_capability_before_execution(
    tmp_path,
    configured_runner,
    advertise,
):
    model = ScriptedModel()
    verifier = FileVerifier(pytest_runner=run_pytest if configured_runner else None)
    result = await service(tmp_path, model, verifier, advertise=advertise).run_graph(
        code_graph(), config(),
    )
    assert result.failure_code == FailureCode.INVALID_OUTPUT
    assert "unsupported" in result.failure_reason
    assert result.tasks == []
    assert model.calls == [] and list(tmp_path.iterdir()) == []
