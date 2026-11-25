"""Mid-test contracts for Role C's scenario tool-whitelist data."""

from __future__ import annotations

from pathlib import Path

import yaml

from tools.registry import build

SCENARIOS_DIR = Path(__file__).resolve().parents[1] / "profiles" / "scenarios"

EXPECTED_SCENARIOS = {
    "s1_software.yaml": {
        "scenario_id": "scenario_1_software",
        "roles": {
            "lead": ["file_read", "file_write"],
            "dev": ["file_write", "file_read"],
            "qa": ["file_read", "file_write"],
            "release": ["file_read", "file_write"],
        },
    },
    "s2_newsroom.yaml": {
        "scenario_id": "scenario_2_newsroom",
        "roles": {
            "chief_editor": ["file_read", "file_write"],
            "reporter": ["web_search", "file_write"],
            "fact_checker": ["web_search", "file_read", "file_write"],
            "editor": ["file_read", "file_write"],
        },
    },
    "s3_fund.yaml": {
        "scenario_id": "scenario_3_fund",
        "roles": {
            "fund_manager": ["file_read", "file_write"],
            "research": ["web_search", "file_write"],
            "risk": ["file_read", "file_write"],
            "marketing": ["file_read", "file_write"],
        },
    },
}


def test_scenario_profile_files_match_the_mid_test_tool_policy() -> None:
    found = sorted(path.name for path in SCENARIOS_DIR.glob("*.yaml"))
    assert found == sorted(EXPECTED_SCENARIOS)

    for filename, expected in EXPECTED_SCENARIOS.items():
        path = SCENARIOS_DIR / filename
        assert yaml.safe_load(path.read_text(encoding="utf-8")) == expected


def test_every_scenario_tool_is_registered() -> None:
    registered = set(build())
    missing = {
        f"{filename}:{role}:{tool}"
        for filename, expected in EXPECTED_SCENARIOS.items()
        for role, tools in expected["roles"].items()
        for tool in tools
        if tool not in registered
    }
    assert not missing, f"scenario profiles declare unregistered tools: {sorted(missing)}"
