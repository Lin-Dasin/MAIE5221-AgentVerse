"""Run pytest inside an agent workspace, for acceptance checks.

This module is verifier-side, not agent-side: it exists so a `pytest_passes`
acceptance rule can be evaluated without handing the agent a tool that runs
arbitrary test commands. It is deliberately not registered in `tools/registry.py`.

Like the tool layer it keeps three properties:

- the target path is validated by `resolve_within`, so a rule cannot point at host files;
- a wall-clock limit is enforced, and hitting it is reported rather than raised;
- it never raises. Every outcome, including a crash, is a dict the caller can read.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from sandbox.paths import PathViolation, resolve_within
from tools.code_exec import (
    CONTAINER_LABEL,
    CONTAINER_PREFIX,
    CPU_LIMIT,
    DEFAULT_IMAGE,
    IMAGE_ENV,
    MEMORY_LIMIT,
    PIDS_LIMIT,
)

DEFAULT_TIMEOUT_SEC = 60.0
BACKEND_ENV = "PYTEST_BACKEND"
MAX_OUTPUT_CHARS = 8000
PASSED_EXIT_CODE = 0
NO_TESTS_EXIT_CODE = 5


def _env(workspace: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            # Keep the workspace importable for local helpers used by the test file.
            "PYTHONPATH": os.pathsep.join(
                [str(workspace), env.get("PYTHONPATH", "")]
            ).strip(os.pathsep),
        }
    )
    return env


def _clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n... truncated {len(text) - MAX_OUTPUT_CHARS} characters"


def _decode(value: str | bytes | None) -> str:
    """Normalise a captured stream from `TimeoutExpired`.

    `subprocess` reports captured output as raw bytes on the timeout path even when
    `text=True` is set, because `_check_timeout()` joins the pipe buffers directly.
    Decoding has to happen before concatenation: mixing a `bytes` stream with a `str`
    one raises `TypeError` inside the error handler.
    """
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _summary(output: str, exit_code: int | None) -> str:
    """Last meaningful line of pytest output, which is where it states the counts."""
    if exit_code == NO_TESTS_EXIT_CODE:
        return "no tests collected"
    for line in reversed([line.strip() for line in output.splitlines() if line.strip()]):
        if "passed" in line or "failed" in line or "error" in line:
            return line
    return "no test summary produced"


def _result(
    *,
    passed: bool,
    exit_code: int | None,
    summary: str,
    output: str,
    started: float,
) -> dict[str, object]:
    return {
        "passed": passed,
        "exit_code": exit_code,
        "summary": summary,
        "output": _clip(output),
        "duration_sec": round(time.monotonic() - started, 3),
    }


def _backend(backend: str | None) -> str:
    value = backend or os.environ.get(BACKEND_ENV, "host")
    value = value.strip().lower()
    if value not in {"host", "docker"}:
        raise ValueError(f"{BACKEND_ENV} must be 'host' or 'docker'")
    return value


def _docker_command(workspace: Path, target: str, container: str) -> list[str]:
    image = os.environ.get(IMAGE_ENV, "").strip() or DEFAULT_IMAGE
    return [
        "docker",
        "run",
        "--rm",
        "--name",
        container,
        "--label",
        CONTAINER_LABEL,
        "--network",
        "none",
        "--memory",
        MEMORY_LIMIT,
        "--memory-swap",
        MEMORY_LIMIT,
        "--cpus",
        CPU_LIMIT,
        "--pids-limit",
        PIDS_LIMIT,
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,size=64m",
        "-v",
        f"{workspace.resolve().as_posix()}:/workspace",
        "-w",
        "/workspace",
        image,
        "python",
        "-m",
        "pytest",
        "-q",
        "--no-header",
        "-p",
        "no:cacheprovider",
        target,
    ]


def _remove_container(name: str) -> None:
    """A killed `docker run` client can leave the container behind; --rm fires on exit only."""
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


def run_pytest(
    workspace: Path,
    target: str = ".",
    *,
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    backend: str | None = None,
) -> dict[str, object]:
    """Run pytest against `target` inside `workspace`.

    `target` is workspace-relative; an absolute or escaping path is rejected and
    reported as a failed result rather than executed.

    Returns:
        ``{"passed": bool, "exit_code": int | None, "summary": str,
        "output": str, "duration_sec": float}``

    ``exit_code`` is ``None`` when the run timed out or pytest could not start.

    ``backend`` selects where pytest runs. ``host`` keeps the previous behaviour;
    ``docker`` runs the same command in the sandbox image, which is the only option that
    isolates generated code. It defaults to ``PYTEST_BACKEND``, then to ``host`` so that a
    machine without a daemon still runs the suite.
    """
    started = time.monotonic()

    try:
        mode = _backend(backend)
    except ValueError as exc:
        # `run_pytest` never raises: a misconfigured backend is a failed result.
        return _result(
            passed=False,
            exit_code=None,
            summary=str(exc),
            output="",
            started=started,
        )


    try:
        resolved = resolve_within(workspace, target)
    except PathViolation as exc:
        return _result(
            passed=False,
            exit_code=None,
            summary=f"invalid target: {exc}",
            output="",
            started=started,
        )

    if not resolved.exists():
        return _result(
            passed=False,
            exit_code=None,
            summary=f"target does not exist: {target}",
            output="",
            started=started,
        )

    container = f"{CONTAINER_PREFIX}{uuid.uuid4().hex[:12]}" if mode == "docker" else None
    if mode == "docker":
        relative = resolved.relative_to(workspace.resolve()).as_posix() or "."
        command = _docker_command(workspace, relative, container or "")
    else:
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
            str(resolved),
        ]

    try:
        proc = subprocess.run(
            command,
            cwd=str(workspace),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            env=_env(workspace),
        )
    except subprocess.TimeoutExpired as exc:
        partial = _decode(exc.stdout) + _decode(exc.stderr)
        if container is not None:
            _remove_container(container)
        return _result(
            passed=False,
            exit_code=None,
            summary=f"timeout after {timeout_sec:g}s",
            output=partial,
            started=started,
        )
    except OSError as exc:
        return _result(
            passed=False,
            exit_code=None,
            summary=f"pytest could not start: {type(exc).__name__}",
            output=str(exc),
            started=started,
        )

    output = f"{proc.stdout}\n{proc.stderr}".strip()
    return _result(
        passed=proc.returncode == PASSED_EXIT_CODE,
        exit_code=proc.returncode,
        summary=_summary(output, proc.returncode),
        output=output,
        started=started,
    )


__all__ = ["DEFAULT_TIMEOUT_SEC", "MAX_OUTPUT_CHARS", "run_pytest"]
