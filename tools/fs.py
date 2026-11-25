"""File tools.

Every handler receives `(arguments, workspace, context)` and must route all
path access through `sandbox.paths.resolve_within`. Handlers raise on bad input;
`SandboxToolExecutor` converts the exception into a failed `ToolResult`.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

from protocol.models import ExecutionContext, ToolDefinition
from sandbox.paths import relative_to, resolve_within

PathArguments = dict[str, Any]

MAX_READ_CHARS = 200_000
MAX_LIST_ENTRIES = 500


def _syntax_diagnostic(target: Path, text: str) -> dict[str, Any]:
    """Return a non-blocking syntax warning for structured source files."""
    suffix = target.suffix.lower()
    if suffix == ".py":
        try:
            ast.parse(text)
        except SyntaxError as exc:
            return {
                "syntax_ok": False,
                "syntax_error": f"line {exc.lineno}: {exc.msg}",
                "syntax_line": exc.lineno,
            }
        return {"syntax_ok": True}
    if suffix == ".json":
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            return {
                "syntax_ok": False,
                "syntax_error": f"line {exc.lineno} column {exc.colno}: {exc.msg}",
                "syntax_line": exc.lineno,
            }
        return {"syntax_ok": True}
    return {}


def _text_argument(arguments: PathArguments, key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value


def read_file(
    arguments: PathArguments, workspace: Path, context: ExecutionContext
) -> dict[str, Any]:
    target = resolve_within(workspace, _text_argument(arguments, "path"))
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"file is not UTF-8 text: {relative_to(workspace, target)}") from exc

    lines = text.splitlines(keepends=True)
    total_lines = len(lines)
    start = arguments.get("start_line")
    end = arguments.get("end_line")
    for label, value in (("start_line", start), ("end_line", end)):
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 1
        ):
            raise ValueError(f"{label} must be a positive integer")
    if start is not None or end is not None:
        first = start or 1
        last = end or total_lines
        if first > last:
            raise ValueError("start_line must not exceed end_line")
        text = "".join(lines[first - 1 : last])
    truncated = len(text) > MAX_READ_CHARS
    if truncated:
        dropped = len(text) - MAX_READ_CHARS
        text = text[:MAX_READ_CHARS] + f"\n... truncated {dropped} characters"

    return {
        "path": relative_to(workspace, target),
        "text": text,
        "truncated": truncated,
        "total_lines": total_lines,
        **({"start_line": start, "end_line": end} if start is not None or end is not None else {}),
    }


def patch_file(
    arguments: PathArguments, workspace: Path, context: ExecutionContext
) -> dict[str, Any]:
    target = resolve_within(workspace, _text_argument(arguments, "path"))
    old_text = _text_argument(arguments, "old_text")
    new_text = arguments.get("new_text")
    if not isinstance(new_text, str):
        raise ValueError("new_text must be a string")
    try:
        original = target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"file is not UTF-8 text: {relative_to(workspace, target)}") from exc
    matches = original.count(old_text)
    if matches == 0:
        raise ValueError(
            "old_text was not found; use file_read with start_line/end_line to inspect "
            "the exact target before patching"
        )
    if matches > 1:
        raise ValueError(
            f"old_text matches {matches} locations; add surrounding context so it matches once"
        )
    updated = original.replace(old_text, new_text, 1)
    target.write_text(updated, encoding="utf-8")
    return {
        "path": relative_to(workspace, target),
        "bytes": len(updated.encode("utf-8")),
        "replacements": 1,
        **_syntax_diagnostic(target, updated),
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
        **_syntax_diagnostic(target, text),
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
    truncated = len(entries) > MAX_LIST_ENTRIES
    if truncated:
        entries = entries[:MAX_LIST_ENTRIES]
    return {
        "path": relative_to(workspace, target) or ".",
        "entries": entries,
        "truncated": truncated,
    }


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
    description=(
        "Read a UTF-8 text file inside your workspace and return its contents. "
        "Output longer than 200k characters is truncated and flagged. "
        "Use start_line/end_line (1-indexed) to read a part of a large file."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Workspace-relative path, for example outputs/draft.md.",
            },
            "start_line": {
                "type": "integer",
                "minimum": 1,
                "description": "Optional first line to return (1-indexed).",
            },
            "end_line": {
                "type": "integer",
                "minimum": 1,
                "description": "Optional last line to return (inclusive, 1-indexed).",
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    },
)

FILE_WRITE = ToolDefinition(
    name="file_write",
    description=(
        "Create or overwrite a UTF-8 text file inside your workspace. "
        "Python and JSON files are syntax-checked after saving; a syntax warning "
        "is returned without blocking the write."
    ),
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

FILE_PATCH = ToolDefinition(
    name="file_patch",
    description=(
        "Replace one exact, uniquely occurring text block in a UTF-8 file. "
        "Use file_read first when the file is large; Python/JSON files are syntax-checked "
        "after saving without blocking the patch."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Workspace-relative file path."},
            "old_text": {
                "type": "string",
                "description": "Exact text that must occur exactly once.",
            },
            "new_text": {"type": "string", "description": "Replacement text."},
        },
        "required": ["path", "old_text", "new_text"],
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
