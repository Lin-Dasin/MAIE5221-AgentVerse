"""Recheck a trimmed recording of the live P2 run, without model credentials."""

import json
from pathlib import Path

import pytest

from harness.p2 import main


@pytest.fixture
def recorded(tmp_path):
    fixture = Path(__file__).parent / "fixtures" / "p2-guided-evidence.json"
    data = json.loads(fixture.read_bytes())
    (tmp_path / "evaluation.json").write_text(json.dumps(data["evaluation"]), encoding="utf-8")
    for name in ("trace", "file-reads"):
        (tmp_path / f"{name}.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in data[name]), encoding="utf-8"
        )
    for relative, content in data["files"].items():
        path = tmp_path / relative
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_bytes(content.encode("utf-8"))
    return tmp_path


def test_guided_recording_passes(recorded):
    assert main(["check", str(recorded)]) == 0
    assert json.loads((recorded / "p2-check.json").read_bytes())["p2_passed"]


@pytest.mark.parametrize("damage", ["missing_audit", "wrong_read", "unpublished", "failed_goal"])
def test_two_handoffs_alone_do_not_pass(recorded, damage):
    audit = recorded / "file-reads.jsonl"
    if damage == "missing_audit":
        audit.unlink()
    elif damage == "wrong_read":
        audit.write_text(audit.read_text().replace("inputs/beta.md", "inputs/alpha.md"))
    elif damage == "unpublished":
        next((recorded / "artifacts").rglob("summary.md")).unlink()
    else:
        path = recorded / "evaluation.json"
        report = json.loads(path.read_bytes())
        report["tasks"][0]["status"] = "failed"
        path.write_text(json.dumps(report))
    assert main(["check", str(recorded)]) == 1
    assert not json.loads((recorded / "p2-check.json").read_bytes())["p2_passed"]
