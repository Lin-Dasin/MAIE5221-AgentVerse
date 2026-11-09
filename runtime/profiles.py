from pathlib import Path

import yaml

from protocol.models import AgentProfile


def load_profiles(directory: Path) -> dict[str, AgentProfile]:
    profiles: dict[str, AgentProfile] = {}
    for path in sorted(directory.glob("*.yaml")):
        profile = AgentProfile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        if profile.agent_id in profiles:
            raise ValueError(f"duplicate agent profile: {profile.agent_id}")
        profiles[profile.agent_id] = profile
    if not profiles:
        raise ValueError("no agent profiles were found")
    return profiles
