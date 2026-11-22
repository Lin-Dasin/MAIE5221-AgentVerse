"""Tests for the sandbox health probes and cleanup."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from sandbox.health import cleanup_stale, container_available


def pending(archive: Path, task_id: str, *, age_sec: float) -> Path:
    """Create an interrupted-publish directory with a controlled mtime."""
    target = archive / "run-1" / f".{task_id}.pending"
    target.mkdir(parents=True)
    (target / "draft.md").write_text("half written", encoding="utf-8")
    stamp = time.time() - age_sec
    os.utime(target, (stamp, stamp))
    return target


def test_stale_pending_directory_is_removed(tmp_path: Path) -> None:
    stale = pending(tmp_path / "artifacts", "t1", age_sec=7200)

    report = cleanup_stale(tmp_path, remove_containers=False)

    assert not stale.exists()
    assert report["removed_dirs"] == [str(stale)]


def test_fresh_pending_directory_is_kept(tmp_path: Path) -> None:
    fresh = pending(tmp_path / "artifacts", "t2", age_sec=60)

    report = cleanup_stale(tmp_path, remove_containers=False)

    assert fresh.exists()
    assert report["removed_dirs"] == []
    assert report["kept_dirs"] == [str(fresh)]


def test_accepted_archives_are_never_touched(tmp_path: Path) -> None:
    accepted = tmp_path / "artifacts" / "run-1" / "t1"
    accepted.mkdir(parents=True)
    (accepted / "notes.md").write_text("finished", encoding="utf-8")
    stamp = time.time() - 7200
    os.utime(accepted, (stamp, stamp))

    cleanup_stale(tmp_path, remove_containers=False)

    assert (accepted / "notes.md").read_text(encoding="utf-8") == "finished"


def test_missing_artifacts_directory_is_not_an_error(tmp_path: Path) -> None:
    report = cleanup_stale(tmp_path, remove_containers=False)

    assert report["removed_dirs"] == []
    assert report["removed_containers"] == []


def test_container_availability_reports_a_reason_when_unusable() -> None:
    available, detail = container_available("agentverse-sandbox-that-does-not-exist:latest")

    if shutil.which("docker") is None:
        assert available is False
        assert "not found on PATH" in detail
        return

    if available:
        pytest.skip("daemon reachable and the probe image exists unexpectedly")
    assert "not found" in detail or "not reachable" in detail or "probe failed" in detail


@pytest.mark.skipif(
    shutil.which("docker") is None,
    reason="docker CLI not available",
)
def test_container_availability_detects_a_missing_image() -> None:
    available, detail = container_available("agentverse-sandbox:definitely-missing")

    assert available is False
    assert "not found" in detail or "not reachable" in detail


def test_orphan_container_cleanup_tolerates_absent_daemon(tmp_path: Path) -> None:
    # Must not raise when docker is unavailable, because cleanup runs at start-up.
    report = cleanup_stale(tmp_path, remove_containers=True)

    assert isinstance(report["removed_containers"], list)


def test_cleanup_reports_paths_as_strings(tmp_path: Path) -> None:
    stale = pending(tmp_path / "artifacts", "t3", age_sec=7200)

    report = cleanup_stale(tmp_path, remove_containers=False)

    assert all(isinstance(item, str) for item in report["removed_dirs"])
    assert str(stale) in report["removed_dirs"]


def test_container_cleanup_requires_exact_names_and_age(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sandbox.health as health_module

    monkeypatch.setattr(
        health_module,
        "_containers_by_name",
        lambda: ["agentverse-abcdef123456", "agentverse-decoy-other", "agentverse-111111111111"],
    )
    monkeypatch.setattr(
        health_module,
        "_created_at",
        lambda name: 1_000.0 if name == "agentverse-abcdef123456" else 9_999.0,
    )
    removed: list[str] = []

    def fake_run(command, **kwargs):
        removed.append(command[-1])
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(health_module.subprocess, "run", fake_run)

    report = health_module.cleanup_stale(tmp_path, older_than_sec=3600, now=10_000.0)

    # only the old, exactly-named, project-owned container is removed
    assert report["removed_containers"] == ["agentverse-abcdef123456"]
    assert removed == ["agentverse-abcdef123456"]
