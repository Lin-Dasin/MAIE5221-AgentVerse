"""Check M2 scoring and the reproducibility of the offline CLI report."""

import json
import subprocess
import sys
from pathlib import Path

from harness.memory_recall import run_memory_recall
from memory.jsonl_memory import JsonlMemory


def test_hits_and_language_totals_match_retrieved_text():
    report = run_memory_recall()
    rows = report["cases"]

    assert {row["language"] for row in rows} == {"en", "zh"}
    assert len({(row["language"], row["case_id"]) for row in rows}) == len(rows)
    for row in rows:
        assert row["expected_marker"]
        assert row["hit"] is (row["expected_marker"] in row["retrieved_text"])

    for language in ("en", "zh"):
        language_rows = [row for row in rows if row["language"] == language]
        summary = report["summary"][language]
        assert summary["total"] == len(language_rows)
        assert summary["hits"] == sum(row["hit"] for row in language_rows)
    assert report["summary"]["en"]["total"] == report["summary"]["zh"]["total"]
    assert report["summary"]["en"]["hits"] > 0  # English positive control.


def test_retrieving_an_unrelated_memory_is_a_miss(monkeypatch):
    async def unrelated(self, agent_id, task):
        return "A different reflection from the same agent."

    monkeypatch.setattr(JsonlMemory, "retrieve", unrelated)
    report = run_memory_recall()

    assert all(row["retrieved_text"] and not row["hit"] for row in report["cases"])
    assert all(report["summary"][language]["hits"] == 0 for language in ("en", "zh"))


def test_cli_writes_utf8_json_and_reproduces_api_measurements(tmp_path: Path):
    report_file = tmp_path / "中文召回.json"
    subprocess.run(
        [sys.executable, "-m", "harness.memory_recall", "--output", str(report_file)],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        timeout=30,
    )
    saved = json.loads(report_file.read_text(encoding="utf-8"))
    repeated = run_memory_recall()

    def hits(report):
        return {(row["language"], row["case_id"]): row["hit"] for row in report["cases"]}

    assert hits(saved) == hits(repeated)
    for language in ("en", "zh"):
        for metric in ("hits", "total"):
            assert saved["summary"][language][metric] == repeated["summary"][language][metric]
