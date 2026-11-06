"""Tests for the Python execution tool."""

from __future__ import annotations

from pathlib import Path

from protocol.models import ExecutionContext, RunConfig, ToolCall, ToolResult
from sandbox.executor import SandboxToolExecutor
from tools.registry import build


def make_context(agent_id: str = "writer") -> ExecutionContext:
    return ExecutionContext(config=RunConfig(), task_id="t1", agent_id=agent_id, attempt=1)


def make_executor(root: Path) -> SandboxToolExecutor:
    return SandboxToolExecutor(root, build())


async def call(
    executor: SandboxToolExecutor, name: str, **arguments: object
) -> ToolResult:
    return await executor.execute(
        ToolCall(call_id="c1", name=name, arguments=arguments), make_context()
    )


async def run(executor: SandboxToolExecutor, code: str, **extra: object) -> ToolResult:
    return await call(executor, "code_exec", code=code, **extra)


async def test_stdout_is_captured(tmp_path: Path) -> None:
    result = await run(make_executor(tmp_path), "print('hello from sandbox')")

    assert result.ok is True
    assert result.data["exit_code"] == 0
    assert "hello from sandbox" in result.data["stdout"]


async def test_non_zero_exit_is_data_not_failure(tmp_path: Path) -> None:
    result = await run(
        make_executor(tmp_path), "import sys\nprint('boom', file=sys.stderr)\nsys.exit(2)"
    )

    assert result.ok is True
    assert result.data["exit_code"] == 2
    assert "boom" in result.data["stderr"]


async def test_timeout_is_a_tool_failure(tmp_path: Path) -> None:
    result = await run(make_executor(tmp_path), "import time; time.sleep(5)", timeout_sec=0.3)

    assert result.ok is False
    assert "exceeded" in (result.error or "")
    assert result.elapsed is not None


async def test_program_runs_inside_workspace(tmp_path: Path) -> None:
    result = await run(
        make_executor(tmp_path),
        "from pathlib import Path\nPath('artifact.txt').write_text('written', encoding='utf-8')",
    )

    assert result.ok is True
    assert (tmp_path / "workspaces" / "writer" / "artifact.txt").read_text() == "written"


async def test_missing_code_is_reported(tmp_path: Path) -> None:
    result = await call(make_executor(tmp_path), "code_exec")

    assert result.ok is False
    assert "missing required argument" in (result.error or "")


async def test_invalid_timeout_is_reported(tmp_path: Path) -> None:
    result = await run(make_executor(tmp_path), "print(1)", timeout_sec=-1)

    assert result.ok is False
    assert "invalid argument" in (result.error or "")


def test_code_exec_is_registered() -> None:
    registry = build()
    assert "code_exec" in registry
    assert registry["code_exec"].required == ("code",)
