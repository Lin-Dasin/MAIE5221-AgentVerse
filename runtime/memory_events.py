"""Bounded, metadata-only notification of A's Worker-memory lifecycle."""

import asyncio
import hashlib
from typing import TypedDict

from protocol.models import MemoryEvent
from protocol.ports import MemoryObserver


class ContextMetadata(TypedDict):
    context_chars: int
    context_sha256: str


def context_metadata(text: str) -> ContextMetadata:
    return {
        "context_chars": len(text),
        "context_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


async def notify_memory(
    observer: MemoryObserver | None, event: MemoryEvent, timeout_sec: float
) -> str | None:
    if observer is None:
        return None
    try:
        async with asyncio.timeout(min(1, timeout_sec)):
            await observer.memory_event(event)
    except Exception as exc:
        return f"Memory observer {event.phase} failed: {type(exc).__name__}"
    return None
