"""Exercise Mid Test intake with portable scenario and profile fixtures."""

import json

import pytest
import yaml

from harness.scenario import load_scenario, load_scenario_profiles

TEAMS = [
    ("scenario_1_software_engineering", ("lead", "dev", "qa", "release")),
    ("scenario_2_newsroom", ("chief_editor", "reporter", "fact_checker", "editor")),
    ("scenario_3_securities_fund", ("fund_manager", "research", "risk", "marketing")),
]
TASKS = ("  Do the first task.\n保留原文。  ", "Extend the earlier result.\nUse its records.")


def write_scenario(root, name, members):
    directory = root / (name + " 2")
    directory.mkdir()
    sections = [
        f"## Label {agent} ({agent})\n\n```text\nRole {agent}.\n第二行。\n```\n"
        for agent in members
    ]
    (directory / "01_role_initialization.md").write_text(
        "# Test team\n\n" + "\n".join(sections), encoding="utf-8-sig"
    )
    for filename, text in zip(("02_task_1.md", "03_task_2.md"), TASKS, strict=True):
        (directory / filename).write_text(f"# Task\n\n```text\n{text}\n```\n", encoding="utf-8-sig")
    return directory


@pytest.mark.parametrize(("name", "members"), TEAMS)
def test_load_original_prompts_and_matching_scenario_policy(tmp_path, name, members):
    directory = write_scenario(tmp_path, name, members)
    scenario = load_scenario(directory)
    assert scenario.scenario_id == name.replace("_", "-")
    assert scenario.title == "Test team"
    assert [role.agent_id for role in scenario.roles] == list(members)
    assert [role.role_label for role in scenario.roles] == [f"Label {agent}" for agent in members]
    assert [(task.task_id, task.text) for task in scenario.tasks] == list(
        zip(("task-1", "task-2"), TASKS, strict=True)
    )
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    tools = {agent: ["file_read"] for agent in members}
    tools[members[1]] = ["file_write"]
    (profiles_dir / "active.yaml").write_text(
        yaml.safe_dump({"scenario_id": name, "roles": tools}), encoding="utf-8"
    )
    (profiles_dir / "other.yaml").write_text(
        "scenario_id: other\nroles: {unrelated: [web_search]}\n", encoding="utf-8"
    )
    profiles = load_scenario_profiles(scenario, profiles_dir)
    assert list(profiles) == list(members)
    for agent, profile in profiles.items():
        assert profile.system_prompt == f"Role {agent}.\n第二行。"
        assert profile.role == "worker"
        assert profile.tools == tools[agent]


def test_full_profiles_keep_configuration_and_use_original_teacher_prompt(tmp_path):
    name, members = TEAMS[0]
    scenario = load_scenario(write_scenario(tmp_path, name, members))
    for agent in members:
        (tmp_path / f"{agent}.yaml").write_text(
            json.dumps(
                {
                    "agent_id": agent,
                    "role": "critic" if agent == "qa" else "worker",
                    "system_prompt": "Generic prompt",
                    "tools": ["file_write"],
                    "capabilities": ["review"],
                }
            ),
            encoding="utf-8",
        )
    profiles = load_scenario_profiles(scenario, tmp_path)
    assert profiles["qa"].role == "critic"
    assert profiles["qa"].system_prompt == "Role qa.\n第二行。"
    assert profiles["qa"].capabilities == ["review"]
    assert profiles["qa"].tools == ["file_write"]
    (tmp_path / "qa.yaml").unlink()
    with pytest.raises(ValueError, match="Missing profiles.*qa"):
        load_scenario_profiles(scenario, tmp_path)


def test_mismatched_policy_reports_role_ids(tmp_path):
    name, members = TEAMS[0]
    scenario = load_scenario(write_scenario(tmp_path, name, members))
    (tmp_path / "s1.yaml").write_text(
        yaml.safe_dump(
            {"scenario_id": name, "roles": {agent: [] for agent in (*members[:3], "x")}}
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"missing=\['release'\], unexpected=\['x'\]"):
        load_scenario_profiles(scenario, tmp_path)


@pytest.mark.parametrize("last_agent", ["instructor", "qa"])
def test_roles_are_distinct_from_each_other_and_the_human(tmp_path, last_agent):
    directory = write_scenario(tmp_path, "scenario_1", ("lead", "dev", "qa", last_agent))
    with pytest.raises(ValueError, match="four distinct agent IDs"):
        load_scenario(directory)


@pytest.mark.parametrize(
    "bad_blocks",
    [
        "No fence",
        "```text\n  \n```",
        "```\none\n```\n```\ntwo\n```",
        "```text\n```\n\n```text\ntwo\n```",
    ],
)
def test_task_requires_one_nonempty_prompt_block(tmp_path, bad_blocks):
    name, members = TEAMS[0]
    directory = write_scenario(tmp_path, name, members)
    (directory / "03_task_2.md").write_text(bad_blocks, encoding="utf-8")
    with pytest.raises(ValueError, match="one non-empty prompt block in 03_task_2.md"):
        load_scenario(directory)
