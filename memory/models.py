"""Private memory records persisted by Role C."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from protocol.models import Contract, FailureCode


class MemoryRecord(Contract):
    """A compact, auditable lesson produced after one terminal task result."""

    memory_id: str = Field(min_length=1, max_length=128)
    agent_id: str = Field(min_length=1, max_length=64)
    task_summary: str = Field(min_length=1, max_length=4000)
    outcome: str = Field(min_length=1, max_length=32)
    execution_summary: str = Field(default="", max_length=2000)
    failure_code: FailureCode | None = None
    effective_strategy: list[str] = Field(default_factory=list, max_length=4)
    avoid: list[str] = Field(default_factory=list, max_length=4)
    reusable_advice: str = Field(default="", max_length=2000)
    evidence_task_id: str = Field(min_length=1, max_length=64)
    created_at: datetime
