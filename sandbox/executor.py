"""Sandbox tool executor.

Implements `protocol.ports.ToolExecutor`. The executor owns argument validation,
per-call timing and error mapping; individual tools own their behaviour and stay
inside the workspace they are handed.

Contract notes:
- `execute` never raises. Every failure becomes `ToolResult(ok=False, error=...)`
  so the agent loop keeps a readable reason instead of a generic exception name.
- The workspace root is `root/workspaces/<agent_id>`, matching
  `orchestrator.artifacts.ArtifactStore.workspace`.
"""

from __future__ import annotations

import inspect
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from protocol.models import ExecutionContext, ToolCall, ToolDefinition, ToolResult
from sandbox.paths import PathViolation

Handler = Callable[[dict[str, Any], Path, ExecutionContext], Any]


class ToolFailure(Exception):
    """Raised by a handler for an expected failure.

    The message is returned to the caller verbatim, without the
    ``invalid argument`` prefix that ``ValueError`` gets. Use it for runtime
    problems (timeout, upstream unreachable) rather than bad arguments.
    """


@dataclass(frozen=True)
class RegisteredTool:
    """A tool definition bound to its implementation."""

    definition: ToolDefinition
    handler: Handler
    required: tuple[str, ...] = ()


class SandboxToolExecutor:
    """Dispatch tool calls into per-agent workspaces under a single root."""

    def __init__(self, root: Path, tools: Mapping[str, RegisteredTool]) -> None:
        self.root = Path(root).resolve()
        self._tools = dict(tools)

    def workspace(self, agent_id: str) -> Path:
        """Private workspace for one agent; the only root its tools may touch."""
        return self.root / "workspaces" / agent_id

    def tool_definitions(self) -> list[ToolDefinition]:
        """Definitions to pass to `orchestrator.bootstrap.build_service`."""
        return [tool.definition for tool in self._tools.values()]

    async def execute(self, call: ToolCall, context: ExecutionContext) -> ToolResult:
        started = time.monotonic()

        def failure(message: str) -> ToolResult:
            return ToolResult(
                ok=False,
                error=message,
                elapsed=round(time.monotonic() - started, 3),
            )

        tool = self._tools.get(call.name)
        if tool is None:
            known = ", ".join(sorted(self._tools)) or "none"
            return failure(f"unknown tool: {call.name} (registered: {known})")

        missing = [name for name in tool.required if name not in call.arguments]
        if missing:
            return failure(f"missing required argument: {', '.join(missing)}")

        workspace = self.workspace(context.agent_id)
        workspace.mkdir(parents=True, exist_ok=True)

        try:
            value = tool.handler(call.arguments, workspace, context)
            if inspect.isawaitable(value):
                value = await value
        except ToolFailure as exc:
            return failure(str(exc))
        except PathViolation as exc:
            return failure(str(exc))
        except FileNotFoundError as exc:
            return failure(f"file not found: {exc.filename or exc}")
        except IsADirectoryError as exc:
            return failure(f"expected a file, got a directory: {exc.filename or exc}")
        except PermissionError as exc:
            return failure(f"permission denied: {exc.filename or exc}")
        except ValueError as exc:
            return failure(f"invalid argument: {exc}")
        except Exception as exc:  # noqa: BLE001 - surface the type, never raise
            return failure(f"{type(exc).__name__}: {exc}")

        return ToolResult(
            ok=True,
            data=value,
            elapsed=round(time.monotonic() - started, 3),
        )


__all__ = ["Handler", "RegisteredTool", "SandboxToolExecutor", "ToolFailure"]
