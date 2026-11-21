"""Run the calculation acceptance controls without Docker or a model provider."""

import json

import pytest

from harness.docker_calculation import EXPECTED_MARKDOWN, PROGRAM, run
from protocol.models import AgentProfile, ModelResponse, ToolCall
from sandbox.executor import RegisteredTool
from tools.code_exec import CODE_EXEC


class CalculationModel:
    def __init__(self, source: str = PROGRAM):
        self.source = source

    async def complete(self, messages, tools, *, max_tokens):
        if messages[-1].role == "user":
            assert any(tool.name == "code_exec" for tool in tools)
            return ModelResponse(
                tool_calls=[
                    ToolCall(
                        call_id="calculation", name="code_exec", arguments={"code": self.source}
                    )
                ]
            )
        assert messages[-1].role == "tool"
        return ModelResponse(content="Done")


@pytest.fixture
def profiles():
    return {
        "planner": AgentProfile(agent_id="planner", role="planner", system_prompt="Plan the work."),
        "coder": AgentProfile(
            agent_id="coder",
            role="worker",
            system_prompt="Run the supplied calculation.",
            tools=["code_exec", "file_read", "file_write"],
        ),
    }


def fake_calculation_tool(calls, *, backend="docker", exit_code=0, markdown=EXPECTED_MARKDOWN):
    def handler(arguments, workspace, context):
        calls.append((arguments["code"], context.config.run_id, context.task_id, context.agent_id))
        (workspace / "outputs/calculation.md").write_bytes(markdown.encode("utf-8"))
        return {"backend": backend, "exit_code": exit_code, "stdout": "55\n"}

    return RegisteredTool(CODE_EXEC, handler, ("code",))


@pytest.mark.parametrize(
    ("backend", "exit_code", "markdown", "expected_pass"),
    [
        pytest.param("docker", 0, EXPECTED_MARKDOWN, True, id="matching-docker-result"),
        pytest.param("subprocess", 0, EXPECTED_MARKDOWN, False, id="wrong-backend"),
        pytest.param("docker", 1, EXPECTED_MARKDOWN, False, id="nonzero-exit"),
        pytest.param("docker", 0, EXPECTED_MARKDOWN + "\n", False, id="different-file-bytes"),
    ],
)
async def test_docker_acceptance_checks_tool_evidence_and_exact_file_bytes(
    tmp_path, profiles, backend, exit_code, markdown, expected_pass
):
    calls = []
    output, passed = await run(
        tmp_path,
        model=CalculationModel(),
        profiles=profiles,
        tool=fake_calculation_tool(calls, backend=backend, exit_code=exit_code, markdown=markdown),
    )

    control = json.loads((output / "control.json").read_bytes())
    assert passed is control["passed"] is expected_pass
    report = json.loads((output / "evaluation.json").read_bytes())
    assert report["mode"] == "offline"
    case = report["tasks"][0]
    assert calls == [(PROGRAM, case["run_id"], "compute", "coder")]
    assert control["mode"] == "offline"
    assert control["run_id"] == case["run_id"]
    assert control["calls"] == [
        {
            "run_id": case["run_id"],
            "task_id": "compute",
            "worker": "coder",
            "tool": "code_exec",
            "executed": True,
            "ok": True,
            "backend": backend,
            "exit_code": exit_code,
            "stdout_matches": True,
        }
    ]
    summary = report["summary"]
    expected_count = int(expected_pass)
    assert (summary["goals_passed"], summary["goals_total"]) == (expected_count, 1)
    assert (summary["subtasks_passed"], summary["subtasks_total"]) == (expected_count, 1)
    assert summary["goal_completion_rate"] == summary["subtask_completion_rate"] == expected_count


async def test_docker_acceptance_blocks_changed_program_before_the_executor(tmp_path, profiles):
    calls = []
    output, passed = await run(
        tmp_path,
        model=CalculationModel(PROGRAM + "\nprint(0)\n"),
        profiles=profiles,
        tool=fake_calculation_tool(calls),
    )

    assert not passed
    assert calls == []
    control = json.loads((output / "control.json").read_bytes())
    assert not control["passed"]
    (attempted_call,) = control["calls"]
    assert not attempted_call["executed"]
    assert attempted_call["backend"] is attempted_call["exit_code"] is None
    report = json.loads((output / "evaluation.json").read_bytes())
    assert report["mode"] == "offline"
    assert report["summary"]["goal_completion_rate"] == 0.0
    assert report["summary"]["subtask_completion_rate"] == 0.0
