"""Exercise the public T3 CLI and independently inspect its local Git remote."""

import json
import os
import subprocess
import sys
from pathlib import Path

from harness.t3_case import fixture_text


def bare_git(remote: Path, *args: str) -> bytes:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return subprocess.run(
        ["git", "--git-dir", str(remote), *args], env=env, capture_output=True, check=True
    ).stdout


def test_local_cli_verifies_published_commit_and_leaves_denied_ref_untouched(tmp_path):
    # If T3 were to trust the caller's remote, the push would change this repository.
    decoy = tmp_path / "caller-remote.git"
    bare_git(decoy, "init", "--bare")
    assert not bare_git(decoy, "for-each-ref")
    secret = "local-t3-test-token-sentinel"
    env = dict(
        os.environ,
        PYTHONIOENCODING="utf-8",
        GITHUB_TOKEN=secret,
        GITHUB_SANDBOX_REPO=str(decoy),
        GIT_DIR=str(tmp_path / "bogus-git-dir"),
        GIT_WORK_TREE=str(tmp_path / "bogus-work-tree"),
        GIT_CONFIG_COUNT="1",
        GIT_CONFIG_KEY_0="init.defaultBranch",
        GIT_CONFIG_VALUE_0="caller-default",
    )
    work_dir = tmp_path / "runs"
    proc = subprocess.run(
        [sys.executable, "-m", "harness.t3", "--work-dir", str(work_dir)],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        encoding="utf-8",
        timeout=120,
    )
    assert secret not in proc.stdout + proc.stderr
    assert proc.returncode == 0, proc.stderr + proc.stdout
    (control_path,) = work_dir.glob("*/control.json")
    batch = control_path.parent
    assert batch.name in proc.stdout
    control = json.loads(control_path.read_bytes())
    assert control["passed"]
    approved, denied = control["approve"], control["deny"]
    for case in (approved, denied):
        assert case["checks"] and all(case["checks"].values())
        assert case["nodes_total"] == 1
    assert (approved["raw_goal"], approved["nodes_passed"]) == ("success", 1)
    assert (denied["raw_goal"], denied["nodes_passed"]) == ("failed", 0)

    remote = batch / "remote.git"
    ref = "refs/heads/" + approved["branch"]
    full_sha = bare_git(remote, "rev-parse", "--verify", ref).decode("ascii").strip()
    assert approved["remote_sha"] == full_sha
    assert approved["commit"] and full_sha.startswith(approved["commit"])
    blob = bare_git(remote, "show", f"{full_sha}:outputs/report.md")
    assert blob == fixture_text(True).encode("utf-8")
    assert fixture_text(True) != fixture_text(False)
    assert denied["refs_before"] == approved["refs_after"] == denied["refs_after"]
    assert (approved["refs_before"] != approved["refs_after"]) and denied["refs_before"]
    assert denied["commit"] and not full_sha.startswith(denied["commit"])
    denied_workspace = batch / "deny" / "workspaces" / "writer"
    assert bare_git(denied_workspace / ".git", "rev-parse", "HEAD^").decode().strip() == full_sha
    assert not bare_git(decoy, "for-each-ref")
    for path in batch.rglob("*"):
        if path.suffix in {".json", ".jsonl"} and path.is_file():
            assert secret.encode() not in path.read_bytes()
