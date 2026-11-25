"""Regression coverage for C's reproducible frozen-history M3 control."""

import asyncio
import json

from scripts.memory_ab import run_memory_ab


def test_memory_ab_changes_only_injection_and_preserves_raw_evidence(tmp_path):
    report = asyncio.run(run_memory_ab(tmp_path))

    assert report["variable"] == "RunConfig.memory_enabled"
    off = report["groups"]["memory-off"]
    on = report["groups"]["memory-on"]
    assert off["memory_enabled"] is False and on["memory_enabled"] is True
    assert off["write_memory"] is on["write_memory"] is False
    assert off["summary"]["goals_passed"] == off["summary"]["subtasks_passed"] == 0
    assert on["summary"]["goals_passed"] == on["summary"]["subtasks_passed"] == 3

    for disabled, enabled in zip(off["cases"], on["cases"], strict=True):
        assert disabled["case_id"] == enabled["case_id"]
        assert disabled["starting_memory_sha256"] == enabled["starting_memory_sha256"]
        assert disabled["marker_absent_from_recall_title"]
        assert enabled["marker_absent_from_recall_title"]
        assert not disabled["model_requests"][0]["marker_in_memory"]
        assert enabled["model_requests"][0]["marker_in_memory"]
        for item in (disabled, enabled):
            raw = tmp_path / item["raw_result"]
            assert raw.is_file()
            assert json.loads(raw.read_text(encoding="utf-8"))["run_id"].startswith("m3-")
