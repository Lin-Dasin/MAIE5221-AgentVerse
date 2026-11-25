"""Session-scoped private lessons for Mid scenario agents.

Each role owns one JSON file under ``<session>/memory/<role>.json``. Lessons are
deliberately generic methodology (no task facts or business data), so recalling
them in a later task cannot leak earlier task content.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_WORD = re.compile(r"[A-Za-z0-9_]+")
_CJK = re.compile(r"[\u4e00-\u9fff]+")
MAX_ENTRIES = 50


def _tokens(text: str) -> set[str]:
    tokens = {word.casefold() for word in _WORD.findall(text)}
    for run in _CJK.findall(text):
        if len(run) == 1:
            tokens.add(run)
        else:
            tokens.update(run[index : index + 2] for index in range(len(run) - 1))
    return tokens


@dataclass(frozen=True)
class MemoryEntry:
    entry_id: str
    timestamp: str
    task_id: str
    outcome: str
    lessons: list[str]
    tags: list[str]


class ScenarioMemory:
    """One private JSON file per scenario agent inside the session directory."""

    def __init__(self, session_dir: Path) -> None:
        self.root = Path(session_dir).resolve() / "memory"
        self.root.mkdir(parents=True, exist_ok=True)
        self.events_path = self.root / "events.jsonl"

    def path_for(self, agent_id: str) -> Path:
        if not _IDENTIFIER.fullmatch(agent_id):
            raise ValueError("agent_id must be a simple identifier")
        return self.root / f"{agent_id}.json"

    def _load(self, agent_id: str) -> list[MemoryEntry]:
        path = self.path_for(agent_id)
        if not path.is_file():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data.get("entries") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            raise ValueError(f"invalid scenario memory file: {path.name}")
        return [
            MemoryEntry(
                entry_id=str(item["entry_id"]),
                timestamp=str(item["timestamp"]),
                task_id=str(item["task_id"]),
                outcome=str(item["outcome"]),
                lessons=[str(value) for value in item.get("lessons", [])],
                tags=[str(value) for value in item.get("tags", [])],
            )
            for item in entries
            if isinstance(item, dict)
        ]

    def _save(self, agent_id: str, entries: list[MemoryEntry]) -> None:
        path = self.path_for(agent_id)
        payload = {"agent_id": agent_id, "entries": [asdict(entry) for entry in entries]}
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def recall(self, agent_id: str, task_text: str, *, limit: int = 3) -> list[MemoryEntry]:
        if limit < 1:
            raise ValueError("limit must be positive")
        entries = self._load(agent_id)
        if not entries:
            return []
        query = _tokens(task_text)

        def score(entry: MemoryEntry) -> int:
            return len(query & _tokens(" ".join(entry.lessons + entry.tags)))

        ranked = sorted(entries, key=lambda entry: (score(entry), entry.timestamp), reverse=True)
        relevant = [entry for entry in ranked if score(entry) > 0][:limit]
        return relevant or ranked[:limit]

    def write(
        self,
        agent_id: str,
        *,
        task_id: str,
        outcome: str,
        lessons: list[tuple[str, tuple[str, ...]]],
    ) -> MemoryEntry:
        entries = self._load(agent_id)
        normalized: list[str] = []
        tags: set[str] = set()
        for lesson, lesson_tags in lessons:
            text = lesson.strip()
            if text and text not in normalized:
                normalized.append(text)
                tags.update(tag for tag in lesson_tags if tag)
        if not normalized:
            normalized = ["Read the exact required artifact and handoff paths before writing."]
            tags.add("handoff")
        entry = MemoryEntry(
            entry_id=uuid4().hex,
            timestamp=datetime.now(UTC).isoformat(),
            task_id=task_id,
            outcome=outcome,
            lessons=normalized[:3],
            tags=sorted(tags),
        )
        entries.append(entry)
        self._save(agent_id, entries[-MAX_ENTRIES:])
        return entry

    def log_event(self, phase: str, agent_id: str, task_id: str, entry_ids: list[str]) -> None:
        if phase not in {"memory_recall", "memory_write"}:
            raise ValueError("unknown memory event phase")
        row = {
            "timestamp": datetime.now(UTC).isoformat(),
            "phase": phase,
            "agent_id": agent_id,
            "task_id": task_id,
            "entry_ids": entry_ids,
        }
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


__all__ = ["MAX_ENTRIES", "MemoryEntry", "ScenarioMemory"]