"""Tests for the file tools and the sandbox executor."""

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


async def test_write_then_read_roundtrip(tmp_path: Path) -> None:
    executor = make_executor(tmp_path)

    written = await call(executor, "file_write", path="outputs/note.md", text="hello 世界")
    assert written.ok is True
    assert written.data["path"] == "outputs/note.md"

    read = await call(executor, "file_read", path="outputs/note.md")
    assert read.ok is True
    assert read.data["text"] == "hello 世界"


async def test_workspace_is_private_per_agent(tmp_path: Path) -> None:
    executor = make_executor(tmp_path)
    await call(executor, "file_write", path="outputs/secret.md", text="only for writer")

    other = await executor.execute(
        ToolCall(call_id="c2", name="file_read", arguments={"path": "outputs/secret.md"}),
        make_context("researcher"),
    )
    assert other.ok is False
    assert "file not found" in (other.error or "")


async def test_relative_traversal_is_rejected(tmp_path: Path) -> None:
    executor = make_executor(tmp_path)

    result = await call(executor, "file_write", path="../escape.md", text="nope")
    assert result.ok is False
    assert "escapes workspace" in (result.error or "")


async def test_absolute_path_outside_is_rejected(tmp_path: Path) -> None:
    executor = make_executor(tmp_path)

    result = await call(executor, "file_read", path=str(tmp_path / "elsewhere.txt"))
    assert result.ok is False
    assert "escapes workspace" in (result.error or "")


async def test_unknown_tool_is_reported(tmp_path: Path) -> None:
    executor = make_executor(tmp_path)

    result = await call(executor, "browser_open", url="https://example.com")
    assert result.ok is False
    assert "unknown tool" in (result.error or "")


async def test_missing_argument_is_reported(tmp_path: Path) -> None:
    executor = make_executor(tmp_path)

    result = await call(executor, "file_write", path="outputs/note.md")
    assert result.ok is False
    assert "missing required argument" in (result.error or "")
    assert "text" in (result.error or "")


async def test_delete_then_read_fails(tmp_path: Path) -> None:
    executor = make_executor(tmp_path)
    await call(executor, "file_write", path="outputs/tmp.md", text="scratch")

    deleted = await call(executor, "file_delete", path="outputs/tmp.md")
    assert deleted.ok is True
    assert deleted.data["deleted"] is True

    read = await call(executor, "file_read", path="outputs/tmp.md")
    assert read.ok is False


async def test_list_returns_entries(tmp_path: Path) -> None:
    executor = make_executor(tmp_path)
    await call(executor, "file_write", path="outputs/a.md", text="a")
    await call(executor, "file_write", path="outputs/b.md", text="b")

    listed = await call(executor, "file_list", path="outputs")
    assert listed.ok is True
    names = sorted(entry["name"] for entry in listed.data["entries"])
    assert names == ["a.md", "b.md"]


def test_definitions_cover_every_registered_tool() -> None:
    registry = build()
    names = {tool.definition.name for tool in registry.values()}
    assert names == {"file_read", "file_write", "file_list", "file_delete"}
    assert registry["file_delete"].definition.requires_approval is True
