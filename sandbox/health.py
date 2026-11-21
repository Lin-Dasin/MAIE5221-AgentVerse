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
import shutil
import subprocess
import time
from pathlib import Path

from tools.code_exec import CONTAINER_PREFIX, DEFAULT_IMAGE, IMAGE_ENV

STALE_AFTER_SEC = 3600.0
PROBE_TIMEOUT_SEC = 20.0


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


def _orphan_containers() -> list[str]:
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
        for name in _orphan_containers():
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
