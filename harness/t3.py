"""Exercise Git publication and denial against a new local bare repository."""

import argparse
import asyncio
import hashlib
import json
import multiprocessing
import os
import subprocess
from pathlib import Path
from uuid import uuid4

from harness.t3_case import fixture_text, run_case


def _git(directory: Path, *arguments: str) -> bytes:
    return subprocess.check_output(
        ["git", "-C", str(directory), *arguments], stderr=subprocess.PIPE, timeout=30
    )


def _refs(bare: Path) -> dict[str, str]:
    records = _git(bare, "for-each-ref", "--format=%(refname) %(objectname)").decode("utf-8")
    return dict(line.split(" ", 1) for line in records.splitlines())


def _object(directory: Path, *arguments: str) -> bytes | None:
    """A missing commit or file is a failed check; keep the rest of the evidence."""
    try:
        return _git(directory, *arguments)
    except subprocess.CalledProcessError:
        return None


async def _run_local_pair(root: Path) -> bool:
    bare = root / "remote.git"
    bare.mkdir()
    # Leave HEAD on a deliberately different branch to exercise explicit ref lookup.
    _git(bare, "init", "--bare", "--initial-branch=unused-default")
    results = {}
    for scenario, approve in (("approve", True), ("deny", False)):
        before = _refs(bare)
        case_root = root / scenario
        workspace = case_root / "workspaces" / "writer"
        if not approve and results["approve"]["checks"]["published_to_reported_branch"]:
            # Make the denied commit eligible for a normal fast-forward push.
            workspace.parent.mkdir(parents=True)
            _git(root, "clone", "--branch", results["approve"]["branch"], str(bare), str(workspace))
        result = await run_case(case_root, approve)
        after = _refs(bare)
        checks = result["checks"]
        branch = result["branch"]
        ref = f"refs/heads/{branch}"
        remote_sha = after.get(ref)
        short_sha = result["commit"]
        local_sha = None
        blob = None
        expected_bytes = fixture_text(approve).encode("utf-8")
        if short_sha:
            resolved = _object(workspace, "rev-parse", "--verify", f"{short_sha}^{{commit}}")
            local_sha = resolved.decode().strip() if resolved else None
        checks["workspace_commit"] = bool(local_sha) and (
            _git(workspace, "rev-parse", "HEAD").decode().strip() == local_sha
            and _object(workspace, "cat-file", "blob", f"{local_sha}:outputs/report.md")
            == expected_bytes
        )
        if remote_sha:
            blob = _object(bare, "cat-file", "blob", f"{remote_sha}:outputs/report.md")
        if approve:
            checks["published_to_reported_branch"] = (
                bool(branch and local_sha)
                and result["push_branch"] == branch
                and after == {**before, ref: local_sha}
                and before.get(ref) != local_sha
            )
            checks["published_blob_matches"] = blob == expected_bytes
        else:
            approved = results["approve"]
            checks["denied_commit_extends_approved_commit"] = bool(local_sha and remote_sha) and (
                _git(workspace, "rev-list", "--parents", "-1", local_sha).decode().split()
                == [local_sha, approved["remote_sha"]]
            )
            checks["existing_ref_preserved"] = (
                before == after
                and branch == approved["branch"]
                and bool(remote_sha)
                and remote_sha == approved["remote_sha"]
                and local_sha != remote_sha
            )
            checks["approved_blob_preserved"] = blob == fixture_text(True).encode("utf-8")
        result.update(
            local_sha=local_sha,
            remote_sha=remote_sha,
            refs_before=before,
            refs_after=after,
            remote_blob_sha256=hashlib.sha256(blob).hexdigest() if blob is not None else None,
        )
        results[scenario] = result
    passed = all(all(case["checks"].values()) for case in results.values())
    output = root / "control.json"
    output.write_text(json.dumps({"passed": passed, **results}, indent=2) + "\n", encoding="utf-8")
    print(f"T3 controls: {'PASS' if passed else 'FAIL'}\nEvidence: {output}", flush=True)
    return passed


def _worker(root: Path) -> None:
    # B's tools read process-level Git settings. A child keeps concurrent runs independent.
    for key in tuple(os.environ):
        if key.upper().startswith(("GIT_", "GITHUB_")):
            del os.environ[key]
    os.environ.update(
        GITHUB_SANDBOX_REPO=str(root / "remote.git"),
        GIT_ALLOW_PROTOCOL="file",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_COUNT="1",
        GIT_CONFIG_KEY_0="init.defaultBranch",
        GIT_CONFIG_VALUE_0="delivery",
    )
    raise SystemExit(int(not asyncio.run(_run_local_pair(root))))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=Path("results/t3"))
    args = parser.parse_args()
    root = args.work_dir.resolve() / uuid4().hex
    root.mkdir(parents=True, exist_ok=False)
    child = multiprocessing.get_context("spawn").Process(target=_worker, args=(root,))
    child.start()
    child.join()
    return child.exitcode or 0


if __name__ == "__main__":
    raise SystemExit(main())
