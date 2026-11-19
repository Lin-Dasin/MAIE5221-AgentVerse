"""Path validation for agent workspaces.

Every tool that touches the filesystem routes through `resolve_within`. Symlinks
are resolved before the containment check, so a link pointing outside the
workspace is rejected exactly like a `..` traversal.
"""

from __future__ import annotations

from pathlib import Path


class PathViolation(Exception):
    """Raised when a requested path escapes the allowed workspace root."""


def resolve_within(root: Path, candidate: str | Path) -> Path:
    """Resolve `candidate` and assert it stays inside `root`.

    `candidate` may be relative (resolved against `root`) or absolute. The
    returned path is absolute and symlink-free.
    """
    base = Path(root).resolve()
    raw = Path(candidate)
    target = (raw if raw.is_absolute() else base / raw).resolve()

    if target != base and base not in target.parents:
        raise PathViolation(f"path escapes workspace: {candidate!r} -> {target}")

    return target


def relative_to(root: Path, target: Path) -> str:
    """Render `target` as a POSIX-style path relative to `root`."""
    return Path(target).resolve().relative_to(Path(root).resolve()).as_posix()
