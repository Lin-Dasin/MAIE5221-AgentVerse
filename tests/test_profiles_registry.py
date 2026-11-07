"""Contract test: every tool a profile declares must exist in the registry.

`AgentRuntime.__init__` rejects a profile whose tool names are not registered, so
a profile written ahead of its tool implementation makes the whole service fail
to start. That happened once with `web_search`; this test makes it loud instead
of leaving it to a group message.
"""

from __future__ import annotations

from pathlib import Path

from runtime.profiles import load_profiles
from tools.registry import build

PROFILES_DIR = Path(__file__).resolve().parents[1] / "profiles"


def test_profiles_directory_is_not_empty() -> None:
    assert list(PROFILES_DIR.glob("*.yaml")), f"no profiles found under {PROFILES_DIR}"


def test_every_declared_tool_is_registered() -> None:
    profiles = load_profiles(PROFILES_DIR)
    assert profiles, "load_profiles returned no agents"

    registry = name_set()
    missing = sorted(
        f"{agent_id} -> {tool}"
        for agent_id, profile in profiles.items()
        for tool in profile.tools
        if tool not in registry
    )
    assert not missing, f"profiles declare unregistered tools: {missing}"


def test_profile_keys_match_agent_ids() -> None:
    profiles = load_profiles(PROFILES_DIR)

    mismatched = sorted(key for key, profile in profiles.items() if key != profile.agent_id)
    assert not mismatched, f"profile file name and agent_id disagree: {mismatched}"


def test_a_planner_and_at_least_one_worker_exist() -> None:
    profiles = load_profiles(PROFILES_DIR)
    roles = [profile.role for profile in profiles.values()]

    assert "planner" in roles, "no planner profile"
    assert roles.count("worker") >= 1, "no worker profile"


def name_set() -> set[str]:
    return {tool.definition.name for tool in build().values()}
