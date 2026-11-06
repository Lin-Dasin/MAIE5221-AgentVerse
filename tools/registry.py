"""Tool registry for Role B.

One place where a tool definition is bound to its handler. `build()` returns the
mapping `sandbox.executor.SandboxToolExecutor` expects; `definitions()` returns
the same tools as `ToolDefinition` objects for `build_service`.
"""

from __future__ import annotations

from protocol.models import ToolDefinition
from sandbox.executor import RegisteredTool
from tools.code_exec import CODE_EXEC, run_code
from tools.fs import (
    FILE_DELETE,
    FILE_LIST,
    FILE_READ,
    FILE_WRITE,
    delete_file,
    list_dir,
    read_file,
    write_file,
)


def build() -> dict[str, RegisteredTool]:
    """Registry of every tool Role B exposes, keyed by tool name."""
    return {
        FILE_READ.name: RegisteredTool(
            definition=FILE_READ,
            handler=read_file,
            required=("path",),
        ),
        FILE_WRITE.name: RegisteredTool(
            definition=FILE_WRITE,
            handler=write_file,
            required=("path", "text"),
        ),
        FILE_LIST.name: RegisteredTool(
            definition=FILE_LIST,
            handler=list_dir,
        ),
        FILE_DELETE.name: RegisteredTool(
            definition=FILE_DELETE,
            handler=delete_file,
            required=("path",),
        ),
        CODE_EXEC.name: RegisteredTool(
            definition=CODE_EXEC,
            handler=run_code,
            required=("code",),
        ),
    }


def definitions(registry: dict[str, RegisteredTool] | None = None) -> list[ToolDefinition]:
    """Tool definitions in registry order, ready for `build_service`."""
    active = registry if registry is not None else build()
    return [tool.definition for tool in active.values()]
