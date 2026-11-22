"""Git tools: commit inside the agent workspace, optionally push to a sandbox repo.

Two tools rather than one with an `action` parameter, because pushing is an
irreversible outward action and must be able to require approval on its own.

Safety rules this module enforces:

- All commands run with `cwd` set to the agent workspace. If the workspace sits
  inside a larger repository (which it does in this project), a nested repository
  is created first and the top-level path is re-checked afterwards. If the top
  level is not the workspace afterwards, the call fails rather than touching the
  host repository.
- `GIT_TERMINAL_PROMPT=0` and an empty `GIT_ASKPASS` prevent git from blocking on
  a credential prompt inside a task.
- The access token is inserted into the push URL at call time and scrubbed from
  any error text before it is returned to the model.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from protocol.models import ExecutionContext, ToolDefinition
from sandbox.executor import ToolFailure

GIT_TIMEOUT_SEC = 30.0
PUSH_TIMEOUT_SEC = 60.0
IDENTITY = (
    "-c",
    "user.name=agentverse",
    "-c",
    "user.email=agentverse@localhost",
    "-c",
    "commit.gpgsign=false",
    # A repository inside the workspace can carry hooks written by generated code, and
    # those hooks run on `git commit` with this process's environment. Point git at a
    # path that is not a directory so no hook is ever found.
    "-c",
    f"core.hooksPath={os.devnull}",
)

# Nothing outside this list reaches a git subprocess. `os.environ` contains the model and
# search keys plus the git token, and a hook or a credential helper is enough to leak them.
SAFE_ENV_KEYS = (
    "PATH",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "TEMP",
    "TMP",
    "TMPDIR",
    "HOME",
    "USERPROFILE",
    "LANG",
    "LC_ALL",
)


def _env() -> dict[str, str]:
    env = {key: os.environ[key] for key in SAFE_ENV_KEYS if key in os.environ}
    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": "",
            "GIT_CONFIG_NOSYSTEM": "1",
            # Ignore the user's global config: it can define hooks, aliases or a credential
            # helper that would run with this process's privileges.
            "GIT_CONFIG_GLOBAL": os.devnull,
            "LC_ALL": "C.UTF-8",
        }
    )
    return env


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret else text


def _git(
    args: list[str], cwd: Path, *, timeout: float = GIT_TIMEOUT_SEC
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *IDENTITY, *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=_env(),
        )
    except subprocess.TimeoutExpired:
        # `str(TimeoutExpired)` embeds the full argv, which for `git_push` contains the
        # access token. Rebuild the message from the subcommand only.
        raise ToolFailure(f"git {args[0]} timed out after {timeout:g}s") from None
    except OSError as exc:
        raise ToolFailure(f"git {args[0]} could not start: {type(exc).__name__}") from None


def _failure(proc: subprocess.CompletedProcess[str], action: str, secret: str = "") -> ToolFailure:
    message = (proc.stderr or proc.stdout or "").strip().splitlines()
    detail = message[-1] if message else f"exit code {proc.returncode}"
    if secret:
        detail = detail.replace(secret, "***")
    return ToolFailure(f"git {action} failed: {detail}")


def _toplevel(workspace: Path) -> Path | None:
    proc = _git(["rev-parse", "--show-toplevel"], workspace)
    if proc.returncode != 0:
        return None
    text = proc.stdout.strip()
    return Path(text) if text else None


def _ensure_repo(workspace: Path) -> None:
    """Make `workspace` its own repository, never the surrounding one."""
    top = _toplevel(workspace)
    if top is None or top.resolve() != workspace.resolve():
        init = _git(["init", "-q"], workspace)
        if init.returncode != 0:
            raise _failure(init, "init")
        top = _toplevel(workspace)
    if top is None or top.resolve() != workspace.resolve():
        raise ToolFailure(
            "workspace is not a standalone git repository; refusing to operate on "
            "the surrounding repository"
        )


def _current_branch(workspace: Path) -> str:
    proc = _git(["rev-parse", "--abbrev-ref", "HEAD"], workspace)
    if proc.returncode != 0:
        raise _failure(proc, "branch lookup")
    return proc.stdout.strip() or "main"


def _with_token(url: str, token: str) -> str:
    if not token or not url.startswith("https://"):
        return url
    return re.sub(r"^https://", f"https://x-access-token:{token}@", url, count=1)


def git_commit(
    arguments: dict[str, Any], workspace: Path, context: ExecutionContext
) -> dict[str, Any]:
    message = arguments.get("message")
    if not isinstance(message, str) or not message.strip():
        raise ValueError("message must be a non-empty string")

    raw_paths = arguments.get("paths", ["outputs"])
    if not isinstance(raw_paths, list) or not raw_paths:
        raise ValueError("paths must be a non-empty list of workspace-relative paths")
    if any(not isinstance(item, str) or not item.strip() for item in raw_paths):
        raise ValueError("paths must contain non-empty strings")

    _ensure_repo(workspace)

    add = _git(["add", "--", *raw_paths], workspace)
    if add.returncode != 0:
        raise _failure(add, "add")

    staged = _git(["diff", "--cached", "--name-only"], workspace)
    files = [line for line in staged.stdout.splitlines() if line.strip()]
    if not files:
        return {"committed": False, "files": [], "message": "nothing to commit"}

    commit = _git(["commit", "-q", "-m", message.strip()], workspace)
    if commit.returncode != 0:
        raise _failure(commit, "commit")

    head = _git(["rev-parse", "--short", "HEAD"], workspace)
    return {
        "committed": True,
        "commit": head.stdout.strip(),
        "branch": _current_branch(workspace),
        "files": files,
        "count": len(files),
    }


def git_push(
    arguments: dict[str, Any], workspace: Path, context: ExecutionContext
) -> dict[str, Any]:
    repo_url = os.environ.get("GITHUB_SANDBOX_REPO", "").strip()
    if not repo_url:
        raise ToolFailure(
            "git_push is not configured: GITHUB_SANDBOX_REPO is missing. "
            "Point it at a sandbox repository, never at the project repository."
        )

    _ensure_repo(workspace)
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    branch = _current_branch(workspace)
    target = _with_token(repo_url, token)

    push = _git(
        ["push", target, f"HEAD:refs/heads/{branch}"],
        workspace,
        timeout=PUSH_TIMEOUT_SEC,
    )
    if push.returncode != 0:
        raise _failure(push, "push", secret=token)

    redacted = _redact(target, token)
    return {
        "pushed": True,
        "branch": branch,
        "remote": redacted,
        # `git push` writes "To <remote>" to stderr, and <remote> is the token URL.
        "output": _redact((push.stderr or push.stdout).strip(), token)[:400],
    }


GIT_COMMIT = ToolDefinition(
    name="git_commit",
    description=(
        "Commit files in your workspace to a local git repository, creating one if "
        "needed. Paths default to the outputs directory. Returns the commit id and "
        "the files included."
    ),
    parameters={
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "Commit message, one line."},
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Workspace-relative paths to stage. Defaults to ['outputs'].",
            },
        },
        "required": ["message"],
        "additionalProperties": False,
    },
)

GIT_PUSH = ToolDefinition(
    name="git_push",
    description=(
        "Push the current branch of your workspace repository to the configured "
        "sandbox remote. Irreversible and outward-facing; requires approval."
    ),
    parameters={"type": "object", "properties": {}, "additionalProperties": False},
    requires_approval=True,
)
