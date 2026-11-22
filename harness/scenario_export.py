"""Export a portable scoring bundle from one public scenario conversation."""

import json
import re
import shutil
import stat
from collections.abc import Mapping, Sequence
from pathlib import Path, PureWindowsPath
from tempfile import TemporaryDirectory

from bus.messages import MessageEvent
from protocol.models import relative_path

_TASKS = ("task-1", "task-2")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_DIALOGUE_FIELDS = {
    "run_requested": "goal",
    "clarification_requested": "question",
    "clarification_answered": "answer",
}
_STATUS_EVENTS = {"run_finished", "run_failed", "run_cancelled", "clarification_timed_out"}
_COLLABORATION = {
    "tasks_assigned": "assignment",
    "tasks_delegated": "delegation",
    "artifacts_handed_off": "handoff",
    "scenario_inputs_ready": "handoff",
}
_VISIBLE_KINDS = {"message", "assignment", "delegation", "handoff", "negotiation"}
_INTERNAL_FIELDS = {"reasoning_content", "tool_call_id", "tool_calls", "tool_results"}


def _identifiers(data: dict, *fields: str) -> dict[str, str]:
    return {
        key: value
        for key in fields
        if isinstance(value := data.get(key), str) and _IDENTIFIER.fullmatch(value)
    }


def _portable_path(path: str) -> str:
    relative_path(path)
    for part in path.split("/"):
        if (
            part.endswith((" ", "."))
            or PureWindowsPath(part).is_reserved()
            or any(ord(char) < 32 or char in '<>"|?*' for char in part)
        ):
            raise ValueError("Expected a portable artifact path")
    return path


def _records(data: dict, field: str) -> list[dict]:
    values = data.get(field)
    if not isinstance(values, list):
        return []
    return [value for value in values if isinstance(value, dict)]


def _handoff(source: dict) -> dict[str, str]:
    info = _identifiers(source, "from_task")
    for key in ("path", "destination"):
        value = source.get(key)
        if isinstance(value, str):
            try:
                info[key] = _portable_path(value)
            except ValueError:
                continue
    return info


def _public_content(event: MessageEvent) -> str:
    try:
        data = json.loads(event.text)
    except (ValueError, RecursionError):
        data = None
    name = data.get("event") if isinstance(data, dict) else None
    if event.kind == "message" and not isinstance(name, str):
        if isinstance(data, dict) and (
            _INTERNAL_FIELDS.intersection(data) or data.get("role") in ("system", "tool")
        ):
            return "Internal event omitted"
        return event.text
    if not isinstance(name, str):
        return f"{event.kind.capitalize()} event"

    details = _identifiers(data, "run_id", "task_id", "agent_id", "request_id", "from_task")
    if event.kind == "message" and name in _DIALOGUE_FIELDS:
        field = _DIALOGUE_FIELDS[name]
        if isinstance(data.get(field), str):
            details[field] = data[field]
    elif event.kind == "message" and name in _STATUS_EVENTS:
        if data.get("status") in ("success", "failed"):
            details["status"] = data["status"]
        details.update(_identifiers(data, "failure_code"))
        for key in ("subtasks_passed", "subtasks_total", "warning_count"):
            if type(data.get(key)) is int and data[key] >= 0:
                details[key] = data[key]
    elif _COLLABORATION.get(name) == event.kind:
        if event.kind == "assignment":
            details.update(_identifiers(data, "final_task_id"))
            details["tasks"] = []
            for task in _records(data, "tasks"):
                item = _identifiers(task, "task_id", "agent_id")
                dependencies = task.get("depends_on")
                if isinstance(dependencies, list):
                    item["depends_on"] = [
                        dep
                        for dep in dependencies
                        if isinstance(dep, str) and _IDENTIFIER.fullmatch(dep)
                    ]
                details["tasks"].append(item)
        elif event.kind == "delegation":
            details["children"] = [
                _identifiers(child, "task_id", "agent_id") for child in _records(data, "children")
            ]
        else:
            details["inputs"] = [_handoff(item) for item in _records(data, "inputs")]
    else:
        return f"{event.kind.capitalize()} event"
    return json.dumps({"event": name, **details}, ensure_ascii=False, indent=2)


def _literal(text: str) -> str:
    longest = max((len(match[0]) for match in re.finditer(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}text\n{text}\n{fence}"


def _transcript(
    scenario_id: str,
    conversation_id: str,
    roles: Mapping[str, str],
    messages: Sequence[MessageEvent],
) -> str:
    if any(event.conversation_id != conversation_id for event in messages):
        raise ValueError("Every message must belong to the scenario conversation")
    sequences = [event.sequence for event in messages]
    if any(type(sequence) is not int or sequence < 1 for sequence in sequences):
        raise ValueError("Message sequences must be positive integers")
    if len(set(sequences)) != len(sequences):
        raise ValueError("Message sequences must be unique")
    roster = "\n".join(f"{agent_id} · {label}" for agent_id, label in sorted(roles.items()))
    sections = [
        "# Group transcript",
        _literal(f"Scenario: {scenario_id}\nConversation: {conversation_id}\n\nRoles\n{roster}"),
    ]
    for event in sorted(messages, key=lambda item: item.sequence):
        if event.kind in _VISIBLE_KINDS:
            speaker = roles.get(event.sender, event.sender)
            details = f"{speaker} ({event.sender}) · {event.timestamp} · {event.kind}"
            sections.extend(
                [f"## Event {event.sequence}", _literal(f"{details}\n\n{_public_content(event)}")]
            )
    return "\n\n".join(sections) + "\n"


def _mode(path: Path) -> int:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or (
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise ValueError("Export paths must not use symbolic links or reparse points")
    return info.st_mode


def _directories(path: Path, *, allow_missing: bool = False) -> None:
    if ".." in path.parts:
        raise ValueError("Export paths must not traverse parent directories")
    for directory in reversed((path, *path.parents)):
        try:
            mode = _mode(directory)
        except FileNotFoundError:
            if allow_missing:
                continue
            raise
        if not stat.S_ISDIR(mode):
            raise ValueError("Expected an export directory")


def _copy_artifacts(source: Path, target: Path) -> int:
    target.mkdir()
    files = 0
    names = set()
    for entry in sorted(source.iterdir()):
        name = _portable_path(entry.name)
        if name.casefold() in names:
            raise ValueError("Artifact names must be distinct on case-insensitive filesystems")
        names.add(name.casefold())
        mode = _mode(entry)
        if stat.S_ISDIR(mode):
            files += _copy_artifacts(entry, target / name)
        elif stat.S_ISREG(mode):
            shutil.copyfile(entry, target / name)
            files += 1
        else:
            raise ValueError("Only regular artifact files and directories may be exported")
    return files


def export_scenario(
    destination: Path,
    *,
    scenario_id: str,
    conversation_id: str,
    roles: Mapping[str, str],
    messages: Sequence[MessageEvent],
    accepted_artifacts: Mapping[str, Path],
) -> Path:
    """Publish the transcript and both accepted trees under a fresh destination.

    The caller supplies history from the verified public group only.
    """
    if set(accepted_artifacts) != set(_TASKS):
        raise ValueError("Export requires accepted artifact directories for task-1 and task-2")
    destination = destination.absolute()
    _directories(destination.parent, allow_missing=True)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    sources = {task: accepted_artifacts[task].absolute() for task in _TASKS}
    for source in sources.values():
        _directories(source)
        if destination.is_relative_to(source):
            raise ValueError("Export destination must be outside the accepted artifact trees")
    transcript = _transcript(scenario_id, conversation_id, roles, messages)
    artifact_paths = {task: f"artifacts/{task}" for task in _TASKS}
    manifest = {
        "schema_version": 1,
        "scenario_id": scenario_id,
        "conversation_id": conversation_id,
        "roles": dict(roles),
        "transcript": "transcript.md",
        "artifacts": artifact_paths,
    }

    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".scenario-export-", dir=destination.parent) as staging:
        prepared = Path(staging) / "bundle"
        (prepared / "artifacts").mkdir(parents=True)
        for task, source in sources.items():
            if not _copy_artifacts(source, prepared / artifact_paths[task]):
                raise ValueError(f"Accepted artifact directory for {task} is empty")
        (prepared / "transcript.md").write_text(transcript, encoding="utf-8", newline="\n")
        (prepared / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(destination)
        prepared.rename(destination)
    return destination
