"""Python execution tool with a switchable backend.

Two backends exist and both are tested:

- ``subprocess`` (default) — runs with the current interpreter inside the workspace.
  Fast, no dependencies, no isolation beyond the working directory.
- ``docker`` — runs one short-lived container per call with the workspace mounted at
  ``/workspace``, no network, a memory cap and a CPU cap. Selected with
  ``CODE_EXEC_BACKEND=docker``.

Result semantics are identical for both:

- A program that finished — with any exit code — is a successful tool call.
  A non-zero exit is data, not a tool error: the agent should read `stderr`,
  fix the code and try again. Wrapping that in `ok=False` would hide the output
  and push the agent to repeat the same mistake.
- A timeout, a failure to start the interpreter, or a container that never ran is a
  real tool failure, so it raises `ToolFailure` and comes back as `ok=False`.

What the docker backend adds: no host network, a memory cap, a CPU cap and a
process tree that dies with the container. What it costs: roughly half a second of
start-up per call, and a dependency on a running Docker daemon.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from protocol.models import ExecutionContext, ToolDefinition
from sandbox.executor import ToolFailure

DEFAULT_TIMEOUT_SEC = 10.0
MAX_TIMEOUT_SEC = 30.0
MAX_OUTPUT_CHARS = 8000

BACKEND_ENV = "CODE_EXEC_BACKEND"
IMAGE_ENV = "CODE_EXEC_IMAGE"
DEFAULT_IMAGE = "agentverse-sandbox:latest"
CONTAINER_PREFIX = "agentverse-"
MEMORY_LIMIT = "512m"
CPU_LIMIT = "1"
CONTAINER_EXIT_CODE = 125


def _timeout_seconds(arguments: dict[str, Any]) -> float:
    requested = arguments.get("timeout_sec", DEFAULT_TIMEOUT_SEC)
    if isinstance(requested, bool) or not isinstance(requested, (int, float)):
        raise ValueError("timeout_sec must be a number")
    if requested <= 0:
        raise ValueError("timeout_sec must be positive")
    return min(float(requested), MAX_TIMEOUT_SEC)


def _clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n... truncated {len(text) - MAX_OUTPUT_CHARS} chars"


def _backend() -> str:
    value = os.environ.get(BACKEND_ENV, "subprocess").strip().lower()
    if value not in {"subprocess", "docker"}:
        raise ValueError(f"{BACKEND_ENV} must be 'subprocess' or 'docker'")
    return value


def _image() -> str:
    return os.environ.get(IMAGE_ENV, "").strip() or DEFAULT_IMAGE


def _mount(workspace: Path) -> str:
    # Docker on Windows accepts forward slashes; backslashes break the -v parsing.
    return f"{workspace.resolve().as_posix()}:/workspace"


def _command(code: str, workspace: Path, backend: str) -> tuple[list[str], str | None]:
    if backend == "subprocess":
        return [sys.executable, "-I", "-c", code], None

    name = f"{CONTAINER_PREFIX}{uuid.uuid4().hex[:12]}"
    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        "--network",
        "none",
        "--memory",
        MEMORY_LIMIT,
        "--cpus",
        CPU_LIMIT,
        "-v",
        _mount(workspace),
        "-w",
        "/workspace",
        _image(),
        "python",
        "-I",
        "-c",
        code,
    ]
    return command, name


def _remove_container(name: str) -> None:
    """A killed `docker run` client can leave the container behind; --rm only fires on exit."""
    try:
        subprocess.run(
            ["docker", "rm", "-f", name],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def run_code(
    arguments: dict[str, Any], workspace: Path, context: ExecutionContext
) -> dict[str, Any]:
    code = arguments.get("code")
    if not isinstance(code, str) or not code.strip():
        raise ValueError("code must be a non-empty string")

    limit = _timeout_seconds(arguments)
    backend = _backend()
    command, container = _command(code, workspace, backend)
    started = time.monotonic()

    try:
        completed = subprocess.run(
            command,
            cwd=str(workspace),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=limit,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = round(time.monotonic() - started, 3)
        if container is not None:
            _remove_container(container)
        raise ToolFailure(
            f"execution exceeded {limit:g}s and was terminated (elapsed {elapsed}s)"
        ) from exc
    except OSError as exc:
        if backend == "docker":
            raise ToolFailure(f"docker backend unavailable: {type(exc).__name__}") from None
        raise ToolFailure(f"could not start the interpreter: {exc}") from exc

    if backend == "docker" and completed.returncode == CONTAINER_EXIT_CODE:
        detail = (completed.stderr or "").strip().splitlines()
        raise ToolFailure(
            "container failed to start: " + (detail[-1] if detail else "exit code 125")
        )

    return {
        "exit_code": completed.returncode,
        "stdout": _clip(completed.stdout),
        "stderr": _clip(completed.stderr),
        "duration_sec": round(time.monotonic() - started, 3),
        "timeout_sec": limit,
        "backend": backend,
    }


CODE_EXEC = ToolDefinition(
    name="code_exec",
    description=(
        "Run a short Python program inside your workspace and return its exit code, "
        "stdout and stderr. Files you write land in the workspace. A non-zero exit "
        "code means the program failed; read stderr and fix it."
    ),
    parameters={
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python source to execute. Keep it short and self-contained.",
            },
            "timeout_sec": {
                "type": "number",
                "description": (
                    f"Wall-clock limit in seconds. Default {DEFAULT_TIMEOUT_SEC:g}, "
                    f"maximum {MAX_TIMEOUT_SEC:g}."
                ),
                "exclusiveMinimum": 0,
                "maximum": MAX_TIMEOUT_SEC,
            },
        },
        "required": ["code"],
        "additionalProperties": False,
    },
)
