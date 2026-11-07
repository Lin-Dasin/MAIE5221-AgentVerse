"""Small, isolated JSONL memory adapter for the shared AgentVerse root."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from memory.models import MemoryRecord
from protocol.models import Task, TaskResult

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_TOKEN = re.compile(r"[A-Za-z0-9_]+")


class JsonlMemory:
    """Persist and retrieve one private memory stream per agent.

    The adapter intentionally uses synchronous local file operations inside its
    small async facade. Memory files are short, local, and accessed once per
    task; avoiding a background executor keeps ordering and failure handling
    deterministic for the current serial scheduler.
    """

    def __init__(self, root: Path, *, max_records: int = 3, max_context_chars: int = 2400):
        if max_records < 1:
            raise ValueError("max_records must be positive")
        if max_context_chars < 1:
            raise ValueError("max_context_chars must be positive")
        self.root = root.resolve()
        self.max_records = max_records
        self.max_context_chars = max_context_chars
        self.diagnostics: list[str] = []

    def _path(self, agent_id: str) -> Path:
        if not _IDENTIFIER.fullmatch(agent_id):
            raise ValueError("agent_id must be a normalized identifier")
        path = self.root / "workspaces" / agent_id / "memory" / f"{agent_id}.jsonl"
        resolved = path.resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("memory path escapes the configured root") from exc
        return resolved

    def path_for(self, agent_id: str) -> Path:
        """Return the agent's memory path for diagnostics and experiments."""
        return self._path(agent_id)

    def _load(self, agent_id: str) -> list[MemoryRecord]:
        path = self._path(agent_id)
        if not path.exists():
            return []
        records: list[MemoryRecord] = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                record = MemoryRecord.model_validate_json(line)
            except ValueError:
                self.diagnostics.append(f"Skipped malformed memory record {path}:{line_number}")
                continue
            if record.agent_id != agent_id:
                self.diagnostics.append(f"Skipped cross-agent memory record {path}:{line_number}")
                continue
            records.append(record)
        return records

    @staticmethod
    def _tokens(value: str) -> set[str]:
        return {token.lower() for token in _TOKEN.findall(value) if len(token) > 1}

    @staticmethod
    def _task_text(task: Task) -> str:
        acceptance = " ".join(
            f"{rule.type} {rule.path} {rule.text or ''}" for rule in task.acceptance
        )
        return " ".join([task.title, acceptance, *task.outputs])

    def _score(self, task: Task, record: MemoryRecord) -> int:
        task_tokens = self._tokens(self._task_text(task))
        record_tokens = self._tokens(
            " ".join(
                [
                    record.task_summary,
                    record.outcome,
                    record.execution_summary,
                    *record.effective_strategy,
                    *record.avoid,
                    record.reusable_advice,
                ]
            )
        )
        return len(task_tokens & record_tokens)

    @staticmethod
    def _format(record: MemoryRecord) -> str:
        lines = [f"- Previous {record.outcome} task: {record.task_summary}"]
        if record.execution_summary:
            lines.append(f"  Summary: {record.execution_summary}")
        if record.failure_code is not None:
            lines.append(f"  Failure code: {record.failure_code.value}")
        if record.effective_strategy:
            lines.append(f"  Effective: {'; '.join(record.effective_strategy)}")
        if record.avoid:
            lines.append(f"  Avoid: {'; '.join(record.avoid)}")
        if record.reusable_advice:
            lines.append(f"  Advice: {record.reusable_advice}")
        return "\n".join(lines)

    async def retrieve(self, agent_id: str, task: Task) -> str:
        """Return bounded prompt-ready memory for one agent, or an empty string."""
        records = self._load(agent_id)
        ranked = sorted(
            ((self._score(task, record), record) for record in records),
            key=lambda item: (item[0], item[1].created_at),
            reverse=True,
        )
        selected = [record for score, record in ranked if score > 0][: self.max_records]
        if not selected:
            return ""
        text = "Relevant private memory:\n"
        for record in selected:
            candidate = f"{text}{self._format(record)}\n"
            if len(candidate) > self.max_context_chars:
                break
            text = candidate
        return text.rstrip()

    async def reflect(self, task: Task, result: TaskResult) -> None:
        """Persist a deterministic reflection from the terminal task result."""
        if result.agent_id != task.assignee:
            raise ValueError("task assignee and result agent do not match")
        status = result.status.value
        failure_code = result.failure_code
        defects = result.critique.defects if result.critique is not None else []
        summary = result.summary.strip() or result.failure_reason or "No execution summary recorded"
        if status == "success":
            effective = [summary[:500]]
            avoid: list[str] = []
            advice = "Repeat the verified approach and preserve the declared output paths."
        else:
            effective = []
            failure = result.failure_reason or "The task did not reach a successful terminal state."
            avoid = [(failure + (f" Defects: {'; '.join(defects)}" if defects else ""))[:700]]
            advice = "Check the failure reason and acceptance defects before retrying."
        record = MemoryRecord(
            memory_id=f"mem-{uuid4().hex}",
            agent_id=result.agent_id,
            task_summary=task.title,
            outcome=status,
            execution_summary=summary,
            failure_code=failure_code,
            effective_strategy=effective,
            avoid=avoid,
            reusable_advice=advice,
            evidence_task_id=result.task_id,
            created_at=datetime.now(UTC),
        )
        path = self._path(result.agent_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(record.model_dump_json() + "\n")


MemoryStore = JsonlMemory
