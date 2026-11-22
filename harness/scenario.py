"""Read the instructor's Mid Test scenarios and apply Role C's tool policies."""

import re
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import Field, model_validator

from protocol.models import AgentProfile, Contract, Identifier

INSTRUCTOR = "instructor"
_ROLE_HEADING = re.compile(r"^## (.+?) \(([^()]+)\)[ \t]*$", re.MULTILINE)
_PROMPT = re.compile(r"^`{3}(?:text)?[ \t]*\n(.*?)^`{3}[ \t]*$", re.MULTILINE | re.DOTALL)
_TASK_FILES = (("task-1", "02_task_1.md"), ("task-2", "03_task_2.md"))


class RoleSpec(Contract):
    agent_id: Identifier
    role_label: str = Field(min_length=1)
    system_prompt: str = Field(min_length=1)


class ScenarioTask(Contract):
    task_id: Literal["task-1", "task-2"]
    text: str = Field(min_length=1)


class ScenarioSpec(Contract):
    scenario_id: Identifier
    title: str = Field(min_length=1)
    roles: tuple[RoleSpec, ...] = Field(min_length=4, max_length=4)
    tasks: tuple[ScenarioTask, ScenarioTask]

    @model_validator(mode="after")
    def validate_team_and_tasks(self) -> Self:
        members = {role.agent_id for role in self.roles}
        if len(members) != 4 or INSTRUCTOR in members:
            raise ValueError("Expected four distinct agent IDs, separate from instructor")
        first, second = self.tasks
        if (first.task_id, second.task_id) != ("task-1", "task-2"):
            raise ValueError("Expected task-1 followed by task-2")
        if first.text == second.text:
            raise ValueError("The two task prompts must be distinct")
        return self


def _read_prompt(text: str, source: str) -> str:
    blocks = _PROMPT.findall(text)
    if len(blocks) != 1 or not blocks[0].strip():
        raise ValueError(f"Expected one non-empty prompt block in {source}")
    return blocks[0].removesuffix("\n")


def _scenario_id(name: str) -> str:
    # The distributed folders have a trailing copy number, e.g. "scenario_2_newsroom 2".
    base = re.sub(r" \d+$", "", name.strip())
    return re.sub(r"[\s_]+", "-", base.lower())


def load_scenario(directory: Path) -> ScenarioSpec:
    """Load the four role prompts and the two ordered tasks from one scenario folder."""
    role_file = directory / "01_role_initialization.md"
    text = role_file.read_text(encoding="utf-8-sig")
    title = re.search(r"^# (.+)$", text, re.MULTILINE)
    if title is None:
        raise ValueError(f"Missing scenario title in {role_file}")
    headings = list(_ROLE_HEADING.finditer(text))
    roles = []
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        roles.append(
            RoleSpec(
                agent_id=heading[2],
                role_label=heading[1],
                system_prompt=_read_prompt(
                    text[heading.end() : end], f"{role_file} ({heading[2]})"
                ),
            )
        )
    tasks = tuple(
        ScenarioTask(
            task_id=task_id,
            text=_read_prompt((directory / filename).read_text(encoding="utf-8-sig"), filename),
        )
        for task_id, filename in _TASK_FILES
    )
    return ScenarioSpec(
        scenario_id=_scenario_id(directory.resolve().name),
        title=title[1],
        roles=tuple(roles),
        tasks=tasks,
    )


class _ScenarioPolicy(Contract):
    scenario_id: Identifier
    roles: dict[Identifier, list[str]]


def load_scenario_profiles(scenario: ScenarioSpec, profiles_dir: Path) -> dict[str, AgentProfile]:
    """Match a per-scenario tool policy or existing AgentProfile files to the teacher's team."""
    members = {role.agent_id for role in scenario.roles}
    policy = None
    profiles = {}
    for path in sorted(profiles_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
        if isinstance(data, dict) and "scenario_id" in data:
            candidate = _ScenarioPolicy.model_validate(data)
            if _scenario_id(candidate.scenario_id) == scenario.scenario_id:
                if policy is not None:
                    raise ValueError(f"Duplicate tool policy for {scenario.scenario_id}")
                policy = candidate
        else:
            profile = AgentProfile.model_validate(data)
            if profile.agent_id in profiles:
                raise ValueError(f"Duplicate agent profile: {profile.agent_id}")
            profiles[profile.agent_id] = profile

    if policy is not None:
        if members != policy.roles.keys():
            raise ValueError(
                f"Tool policy for {scenario.scenario_id} must match the four teacher IDs; "
                f"missing={sorted(members - policy.roles.keys())}, "
                f"unexpected={sorted(policy.roles.keys() - members)}"
            )
        if members.intersection(profiles):
            raise ValueError(
                f"Found both full profiles and a tool policy for {scenario.scenario_id}"
            )
        return {
            role.agent_id: AgentProfile(
                agent_id=role.agent_id,
                role="worker",
                system_prompt=role.system_prompt,
                tools=policy.roles[role.agent_id],
            )
            for role in scenario.roles
        }

    missing = sorted(members - profiles.keys())
    if missing:
        raise ValueError(
            f"Missing profiles for {scenario.scenario_id} in {profiles_dir}: {missing}"
        )
    return {
        role.agent_id: profiles[role.agent_id].model_copy(
            update={"system_prompt": role.system_prompt}
        )
        for role in scenario.roles
    }
