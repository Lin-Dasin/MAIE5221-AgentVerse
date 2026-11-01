"""File tools.

Every handler receives `(arguments, workspace, context)` and must route all
path access through `sandbox.paths.resolve_within`. Handlers raise on bad input;
`SandboxToolExecutor` converts the exception into a failed `ToolResult`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from protocol.models import ExecutionContext, ToolDefinition
from sandbox.paths import relative_to, resolve_within

PathArguments = dict[str, Any]


def _text_argument(arguments: PathArguments, key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value


def read_file(
    arguments: PathArguments, workspace: Path, context: ExecutionContext
) -> dict[str, Any]:
    target = resolve_within(workspace, _text_argument(arguments, "path"))
    return {
        "path": relative_to(workspace, target),
        "text": target.read_text(encoding="utf-8"),
    }


def write_file(
    arguments: PathArguments, workspace: Path, context: ExecutionContext
) -> dict[str, Any]:
    target = resolve_within(workspace, _text_argument(arguments, "path"))
    text = arguments.get("text")
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return {
        "path": relative_to(workspace, target),
        "bytes": len(text.encode("utf-8")),
    }


def list_dir(
    arguments: PathArguments, workspace: Path, context: ExecutionContext
) -> dict[str, Any]:
    raw = arguments.get("path", ".")
    if not isinstance(raw, str):
        raise ValueError("path must be a string")
    target = resolve_within(workspace, raw or ".")
    if not target.is_dir():
        raise ValueError(f"not a directory: {relative_to(workspace, target)}")

    entries = [
        {"name": child.name, "type": "dir" if child.is_dir() else "file"}
        for child in sorted(target.iterdir(), key=lambda item: (item.is_file(), item.name))
    ]
    return {"path": relative_to(workspace, target) or ".", "entries": entries}


def delete_file(
    arguments: PathArguments, workspace: Path, context: ExecutionContext
) -> dict[str, Any]:
    target = resolve_within(workspace, _text_argument(arguments, "path"))
    if not target.exists():
        raise ValueError(f"does not exist: {relative_to(workspace, target)}")
    target.unlink()
    return {"path": relative_to(workspace, target), "deleted": True}


FILE_READ = ToolDefinition(
    name="file_read",
    description="Read a UTF-8 text file inside your workspace and return its contents.",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Workspace-relative path, for example outputs/draft.md.",
            }
        },
        "required": ["path"],
        "additionalProperties": False,
    },
)

FILE_WRITE = ToolDefinition(
    name="file_write",
    description="Create or overwrite a UTF-8 text file inside your workspace.",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Workspace-relative path. Parent directories are created.",
            },
            "text": {"type": "string", "description": "Full file contents to write."},
        },
        "required": ["path", "text"],
        "additionalProperties": False,
    },
)

FILE_LIST = ToolDefinition(
    name="file_list",
    description="List files and directories inside your workspace.",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Workspace-relative directory. Defaults to the workspace root.",
            }
        },
        "additionalProperties": False,
    },
)

FILE_DELETE = ToolDefinition(
    name="file_delete",
    description="Delete a single file inside your workspace. Destructive; requires approval.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Workspace-relative file path."}
        },
        "required": ["path"],
        "additionalProperties": False,
    },
    requires_approval=True,
)
