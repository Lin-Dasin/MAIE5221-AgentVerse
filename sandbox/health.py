"""Health probes and cleanup for the sandbox.

Two narrow jobs, both about failure that has already happened rather than preventing it:

- `container_available()` answers "can this machine run the docker backend" before the
  first task fails with a cryptic exit code 125.
- `cleanup_stale()` removes what a killed run leaves behind: `.pending` archive
  directories that never got renamed, and containers whose client died before `--rm`
  could fire.

Nothing here recovers a run. Recovery is a higher-level concern and is out of scope for
this milestone; see `docs/report/section-3.md`.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from tools.code_exec import CONTAINER_PREFIX, DEFAULT_IMAGE, IMAGE_ENV

STALE_AFTER_SEC = 3600.0
PROBE_TIMEOUT_SEC = 20.0
NAME_PATTERN = re.compile(rf"^{re.escape(CONTAINER_PREFIX)}[0-9a-f]{{6,}}$")


def _image(image: str | None) -> str:
    return image or os.environ.get(IMAGE_ENV, "").strip() or DEFAULT_IMAGE


def container_available(image: str | None = None) -> tuple[bool, str]:
    """Return ``(available, detail)``.

    ``detail`` is the resolved image name when available, otherwise a reason a caller
    can print without translating it.
    """
    if shutil.which("docker") is None:
        return False, "docker CLI not found on PATH"

    try:
        daemon = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SEC,
        )
    except (OSError, subprocess.SubprocessError):
        return False, "docker daemon not reachable"
    if daemon.returncode != 0:
        return False, "docker daemon not reachable"

    resolved = _image(image)
    try:
        probe = subprocess.run(
            ["docker", "image", "inspect", resolved],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SEC,
        )
    except (OSError, subprocess.SubprocessError):
        return False, "docker image probe failed"
    if probe.returncode != 0:
        return (
            False,
            f"image {resolved} not found; build it with: docker build -t {resolved} docker/",
        )
    return True, resolved


def _containers_by_name() -> list[str]:
    listing = [
        "docker",
        "ps",
        "-a",
        "--filter",
        f"name={CONTAINER_PREFIX}",
        "--format",
        "{{.Names}}",
    ]
    try:
        listed = subprocess.run(
            listing,
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SEC,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if listed.returncode != 0:
        return []
    return [name.strip() for name in listed.stdout.splitlines() if name.strip()]


def _created_at(name: str) -> float | None:
    """Creation time of a container, or None when it cannot be read."""
    try:
        inspected = subprocess.run(
            ["docker", "inspect", "--format", "{{.Created}}", name],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SEC,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if inspected.returncode != 0:
        return None
    raw = inspected.stdout.strip()
    if not raw:
        return None
    # Docker returns RFC 3339 with nanoseconds: 2026-11-22T07:04:05.123456789Z
    if "." in raw:
        head, _, tail = raw.partition(".")
        suffix = tail[-1] if tail and tail[-1] in "Z+-" else "Z"
        raw = f"{head}{suffix}"
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def cleanup_stale(
    root: Path,
    *,
    older_than_sec: float = STALE_AFTER_SEC,
    now: float | None = None,
    remove_containers: bool = True,
) -> dict[str, object]:
    """Remove leftovers from interrupted runs under ``root``.

    A `.pending` directory is removed only when it is older than ``older_than_sec``, so a
    concurrent run that is mid-publish keeps its own directory. Containers are matched by
    the `agentverse-` name prefix, which only this project uses.
    """
    moment = time.time() if now is None else now
    removed_dirs: list[str] = []
    kept_dirs: list[str] = []

    artifacts = Path(root) / "artifacts"
    if artifacts.is_dir():
        for pending in sorted(artifacts.rglob(".*.pending")):
            if not pending.is_dir():
                continue
            age = moment - pending.stat().st_mtime
            if age < older_than_sec:
                kept_dirs.append(str(pending))
                continue
            shutil.rmtree(pending, ignore_errors=True)
            removed_dirs.append(str(pending))

    removed_containers: list[str] = []
    if remove_containers:
        for name in _containers_by_name():
            # `docker ps --filter name=` is a substring match, so the shape is re-checked
            # before anything is removed: only `<prefix><hex>` belongs to this project.
            if NAME_PATTERN.fullmatch(name) is None:
                continue
            created = _created_at(name)
            # A container started by another run on this machine must survive; only
            # containers older than the threshold are treated as leftovers.
            if created is None or moment - created < older_than_sec:
                continue
            try:
                subprocess.run(
                    ["docker", "rm", "-f", name],
                    capture_output=True,
                    text=True,
                    timeout=PROBE_TIMEOUT_SEC,
                )
            except (OSError, subprocess.SubprocessError):
                continue
            removed_containers.append(name)

    return {
        "removed_dirs": removed_dirs,
        "kept_dirs": kept_dirs,
        "removed_containers": removed_containers,
    }


__all__ = ["STALE_AFTER_SEC", "cleanup_stale", "container_available"]
