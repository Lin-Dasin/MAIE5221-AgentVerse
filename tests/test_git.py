"""Tests for the git tools.

Real `git` is used, but only against temporary directories and a local bare
repository, so nothing touches the network or the project repository.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from protocol.models import ExecutionContext, RunConfig, ToolCall, ToolResult
from sandbox.executor import SandboxToolExecutor, ToolFailure
from tools import git as git_tools
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


def git(*args: str, cwd: Path) -> str:
    proc = subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@localhost", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


async def test_commit_creates_repository_and_commits_outputs(tmp_path: Path) -> None:
    executor = make_executor(tmp_path)
    await call(executor, "file_write", path="outputs/report.md", text="content")

    result = await call(executor, "git_commit", message="add report")

    assert result.ok is True
    assert result.data["committed"] is True
    assert result.data["files"] == ["outputs/report.md"]
    assert result.data["count"] == 1

    workspace = tmp_path / "workspaces" / "writer"
    assert (workspace / ".git").is_dir()
    assert git("log", "--oneline", cwd=workspace).strip().endswith("add report")


async def test_commit_ignores_inputs_by_default(tmp_path: Path) -> None:
    executor = make_executor(tmp_path)
    await call(executor, "file_write", path="outputs/report.md", text="mine")
    await call(executor, "file_write", path="inputs/upstream.md", text="theirs")

    result = await call(executor, "git_commit", message="only outputs")

    assert result.ok is True
    assert result.data["files"] == ["outputs/report.md"]


async def test_second_commit_without_changes_is_not_an_error(tmp_path: Path) -> None:
    executor = make_executor(tmp_path)
    await call(executor, "file_write", path="outputs/report.md", text="content")
    await call(executor, "git_commit", message="first")

    second = await call(executor, "git_commit", message="second")

    assert second.ok is True
    assert second.data["committed"] is False
    assert second.data["files"] == []


async def test_workspace_inside_another_repository_does_not_touch_it(tmp_path: Path) -> None:
    outer = tmp_path / "outer"
    outer.mkdir()
    git("init", "-q", cwd=outer)
    (outer / "keep.txt").write_text("outer", encoding="utf-8")
    git("add", "keep.txt", cwd=outer)
    git("commit", "-q", "-m", "outer commit", cwd=outer)
    outer_head = git("rev-parse", "HEAD", cwd=outer).strip()

    executor = make_executor(outer)
    await call(executor, "file_write", path="outputs/report.md", text="nested")
    result = await call(executor, "git_commit", message="nested commit")

    assert result.ok is True
    assert git("rev-parse", "HEAD", cwd=outer).strip() == outer_head
    assert "outputs/report.md" not in git("ls-tree", "-r", "--name-only", "HEAD", cwd=outer)
    status = git("status", "--porcelain", cwd=outer)
    assert "workspaces/" in status


async def test_push_to_local_bare_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox = tmp_path / "sandbox.git"
    git("init", "-q", "--bare", str(sandbox), cwd=tmp_path)
    monkeypatch.setenv("GITHUB_SANDBOX_REPO", str(sandbox))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    executor = make_executor(tmp_path / "run")
    await call(executor, "file_write", path="outputs/report.md", text="pushed")
    await call(executor, "git_commit", message="ready to push")

    result = await call(executor, "git_push")

    assert result.ok is True
    assert result.data["pushed"] is True
    assert result.data["branch"] in {"main", "master"}
    assert git("log", "--oneline", cwd=sandbox).strip()


async def test_push_without_configuration_fails_clearly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_SANDBOX_REPO", raising=False)

    result = await call(make_executor(tmp_path), "git_push")

    assert result.ok is False
    assert "GITHUB_SANDBOX_REPO is missing" in (result.error or "")


def test_token_is_only_injected_for_https_urls() -> None:
    assert (
        git_tools._with_token("https://github.com/acme/sandbox.git", "tok")
        == "https://x-access-token:tok@github.com/acme/sandbox.git"
    )
    assert git_tools._with_token("/tmp/sandbox.git", "tok") == "/tmp/sandbox.git"
    assert git_tools._with_token("https://github.com/acme/sandbox.git", "") == (
        "https://github.com/acme/sandbox.git"
    )


def test_push_requires_approval() -> None:
    registry = build()
    assert registry["git_push"].definition.requires_approval is True
    assert registry["git_commit"].definition.requires_approval is False


def test_git_timeout_cannot_leak_the_push_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`str(TimeoutExpired)` embeds argv, and git_push puts the token in argv."""
    token = "ghp_secret_value"
    url = f"https://x-access-token:{token}@github.com/acme/sandbox.git"

    def explode(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd=["git", "push", url], timeout=30)

    monkeypatch.setattr(git_tools.subprocess, "run", explode)

    with pytest.raises(ToolFailure) as caught:
        git_tools._git(["push", url], tmp_path)

    assert token not in str(caught.value)
    assert "timed out" in str(caught.value)

def test_git_subprocess_environment_excludes_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("SEARCH_API_KEY", "tvly-should-not-leak")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-should-not-leak")

    env = git_tools._env()

    for key in ("LLM_API_KEY", "SEARCH_API_KEY", "GITHUB_TOKEN"):
        assert key not in env
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_CONFIG_GLOBAL"] == os.devnull


async def test_commit_does_not_run_workspace_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor = make_executor(tmp_path)
    await call(executor, "file_write", path="outputs/first.md", text="one")
    await call(executor, "git_commit", message="create repository")

    workspace = tmp_path / "workspaces" / "writer"
    marker = workspace / "hook-ran.txt"
    hooks = workspace / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "pre-commit").write_text(
        f"#!/bin/sh\necho ran > \"{marker.as_posix()}\"\n", encoding="utf-8", newline="\n"
    )

    await call(executor, "file_write", path="outputs/second.md", text="two")
    result = await call(executor, "git_commit", message="second")

    assert result.ok is True
    assert result.data["committed"] is True
    assert not marker.exists()


async def test_push_output_does_not_echo_the_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = "ghp_very_secret_value"
    monkeypatch.setenv("GITHUB_SANDBOX_REPO", "https://github.com/acme/sandbox.git")
    monkeypatch.setenv("GITHUB_TOKEN", token)

    executor = make_executor(tmp_path)
    await call(executor, "file_write", path="outputs/report.md", text="content")
    await call(executor, "git_commit", message="ready")

    real_git = git_tools._git

    def fake_git(args, cwd, *, timeout=git_tools.GIT_TIMEOUT_SEC):
        if args[0] == "push":
            url = args[1]
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout="",
                stderr=f"To {url}\n   abc1234..def5678  main -> main\n",
            )
        return real_git(args, cwd, timeout=timeout)

    monkeypatch.setattr(git_tools, "_git", fake_git)

    result = await call(executor, "git_push")

    assert result.ok is True
    assert token not in json.dumps(result.data)
    assert "***" in str(result.data["output"])


def test_git_commands_disable_hooks_through_config() -> None:
    # Cross-platform guard: the integration test above relies on the shell running the
    # hook, which Windows may not do even when hooks are enabled.
    assert any("core.hooksPath" in part for part in git_tools.IDENTITY)
