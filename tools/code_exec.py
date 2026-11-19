"""Python execution tool.

Runs a short program with the current interpreter inside the agent workspace and
returns what it printed. The wall-clock limit is enforced by the parent process.

Result semantics:
- A program that finished — with any exit code — is a successful tool call.
  A non-zero exit is data, not a tool error: the agent should read `stderr`,
  fix the code and try again. Wrapping that in `ok=False` would hide the output
  and push the agent to repeat the same mistake.
- A timeout or an inability to start the interpreter is a real tool failure, so
  it raises `ToolFailure` and comes back as `ok=False`.

Known limits of this backend: memory is not capped, and a program that spawns
long-lived children can outlive the timeout. Both belong to the container
backend, which is a later milestone.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from protocol.models import ExecutionContext, ToolDefinition
from sandbox.executor import ToolFailure

DEFAULT_TIMEOUT_SEC = 10.0
MAX_TIMEOUT_SEC = 30.0
MAX_OUTPUT_CHARS = 8000


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


def run_code(
    arguments: dict[str, Any], workspace: Path, context: ExecutionContext
) -> dict[str, Any]:
    code = arguments.get("code")
    if not isinstance(code, str) or not code.strip():
        raise ValueError("code must be a non-empty string")

    limit = _timeout_seconds(arguments)
    started = time.monotonic()

    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-c", code],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=limit,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = round(time.monotonic() - started, 3)
        raise ToolFailure(
            f"execution exceeded {limit:g}s and was terminated (elapsed {elapsed}s)"
        ) from exc
    except OSError as exc:
        raise ToolFailure(f"could not start the interpreter: {exc}") from exc

    return {
        "exit_code": completed.returncode,
        "stdout": _clip(completed.stdout),
        "stderr": _clip(completed.stderr),
        "duration_sec": round(time.monotonic() - started, 3),
        "timeout_sec": limit,
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
                "minimum": 0.1,
                "maximum": MAX_TIMEOUT_SEC,
            },
        },
        "required": ["code"],
        "additionalProperties": False,
    },
)
