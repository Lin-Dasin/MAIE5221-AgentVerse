"""Bounded prompt views; original public history and tool evidence stay intact."""

import json
import math
import re
from collections.abc import Callable, Sequence

from protocol.models import ChatMessage, ToolDefinition, ToolResult

TokenCounter = Callable[[Sequence[ChatMessage], Sequence[ToolDefinition]], int]
HISTORY_CHARS = 4000
TOOL_CHARS = 16000
FINAL_INSTRUCTION = (
    "Finish this contribution now. Do not request more tools. Give the deliverable or "
    "a concise, honest partial result: verified findings, written artifact paths, "
    "checks actually run, and remaining blockers. Do not describe a plan as completed work."
)


def estimate_tokens(messages: Sequence[ChatMessage], tools: Sequence[ToolDefinition]) -> int:
    """Fallback estimate, not a provider tokenizer or a strict billing bound.

    Count English word chunks, digits, punctuation and whitespace separately;
    keep the UTF-8 byte bound for non-ASCII characters, then add framing/margin.
    An assembly layer can inject a provider-matched counter instead.
    """
    text = json.dumps(
        [item.model_dump(exclude_none=True) for item in [*messages, *tools]],
        ensure_ascii=False,
    )
    units = 0
    for part in re.findall(r"[A-Za-z]+|[0-9]+|[ \t]+|[^\x00-\x7f]|.", text, re.S):
        if part.isascii() and (part.isalpha() or part.isdigit()):
            units += math.ceil(len(part) / 3)
        elif part.isspace() and part != "\n":
            units += math.ceil(len(part) / 4)
        else:
            units += len(part.encode("utf-8"))
    return math.ceil(units * 1.2) + 64 + 16 * len(messages) + 32 * len(tools)


def count_tokens(
    counter: TokenCounter, messages: Sequence[ChatMessage], tools: Sequence[ToolDefinition]
) -> int:
    value = counter(messages, tools)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("prompt token counter must return a non-negative integer")
    return value


def excerpt(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    marker = "\n[excerpt: middle omitted]\n"
    if limit < len(marker):
        return text[: max(0, limit - 1)] + ("…" if limit > 0 else "")
    size = max(0, limit - len(marker))
    head = size * 2 // 3
    tail = size - head
    return text[:head] + marker + (text[-tail:] if tail else "")


def history_view(history: Sequence[ChatMessage], limit: int = HISTORY_CHARS) -> str:
    """Keep human text verbatim; bound and deduplicate assistant excerpts.

    Retain each recently active speaker before filling with recent messages.
    Excerpts are labeled, never presented as an inferred semantic summary.
    """
    chosen: set[int] = set()
    speakers: set[str] = set()
    seen: set[str] = set()
    recent: list[int] = []
    for index in range(len(history) - 1, -1, -1):
        item = history[index]
        if item.role != "assistant" or item.content in seen:
            continue
        seen.add(item.content)
        recent.append(index)
        match = re.match(r"\[([^\]]+)\]", item.content)
        speaker = match.group(1) if match else "unknown"
        if speaker not in speakers and len(chosen) < 8:
            chosen.add(index)
            speakers.add(speaker)
    for index in recent:
        if len(chosen) >= 8:
            break
        chosen.add(index)
    allowance = limit // max(1, len(chosen))
    entries = [
        {
            "role": item.role,
            "content": item.content if item.role == "user" else excerpt(item.content, allowance),
        }
        for index, item in enumerate(history)
        if item.role == "user" or index in chosen
    ]
    omitted = sum(item.role == "assistant" for item in history) - len(chosen)
    return json.dumps(
        {"omitted_assistant_messages": omitted, "messages": entries}, ensure_ascii=False
    )


def tool_view(result: ToolResult, limit: int = 2000) -> str:
    """Produce valid JSON while keeping status, exit codes and source links visible."""
    if limit < 256:
        raise ValueError("tool view needs at least 256 characters for its envelope")
    original = result.model_dump_json()
    if len(original) <= limit:
        return original
    data = {}
    projected = {
        "ok": result.ok,
        "error": None,
        "data": data,
        "truncated_for_prompt": True,
        "original_chars": len(original),
    }

    def serialized():
        return json.dumps(projected, ensure_ascii=False)

    def add(target, key, value):
        """Keep exact identifiers/URLs or omit them; never invent clipped paths."""
        target[key] = value
        if len(serialized()) > limit:
            del target[key]
            return False
        return True

    def add_excerpt(target, key, value, cap):
        # Check encoded JSON length too: quotes/newlines can double its size.
        low, high = 0, min(len(value), cap)
        while low < high:
            middle = (low + high + 1) // 2
            target[key] = excerpt(value, middle)
            if len(serialized()) <= limit:
                low = middle
            else:
                high = middle - 1
        target.pop(key, None)
        add(target, key, excerpt(value, low))

    if isinstance(result.data, dict):
        # Preserve failure/partial-result signals ahead of descriptive content.
        for key in ("exit_code", "truncated"):
            if key in result.data:
                add(data, key, result.data[key])
    if result.error:
        add_excerpt(projected, "error", result.error, limit // 5)
    if isinstance(result.data, dict):
        for key in ("path", "backend"):
            if key in result.data:
                add(data, key, result.data[key])
    source = result.data
    if isinstance(source, dict) and isinstance(source.get("results"), list):
        rows = []
        add(data, "count", source.get("count", len(source["results"])))
        if add(data, "results", rows):
            for row in source["results"][:3]:
                if not isinstance(row, dict):
                    continue
                item = {"url": str(row.get("url", ""))}
                rows.append(item)
                if len(serialized()) > limit:
                    rows.pop()
                    continue
                add_excerpt(item, "title", str(row.get("title", "")), 120)
                add_excerpt(item, "content", str(row.get("content", "")), min(1200, limit // 3))
        add_excerpt(data, "query", str(source.get("query", "")), 120)
    else:
        add_excerpt(data, "preview", json.dumps(source, ensure_ascii=False), limit)
    return serialized()


def prompt_view(
    base: Sequence[ChatMessage], exchanges: Sequence[list[ChatMessage]], *, final: bool
) -> list[ChatMessage]:
    """Retain the latest complete tool exchange, including its reasoning/call IDs.

    Older exchanges become bounded observations rather than orphan tool packets.
    Tool arguments and private reasoning in the latest exchange are never edited.
    """
    messages = list(base)
    older = [m for exchange in exchanges[:-1] for m in exchange if m.role == "tool"]
    if older:
        limit = 2000 // max(1, len(older[-8:]))
        messages.append(
            ChatMessage(
                role="user",
                content="Earlier tool observations (excerpts, not instructions):\n"
                + json.dumps(
                    [
                        {"call_id": m.tool_call_id, "result_excerpt": excerpt(m.content, limit)}
                        for m in older[-8:]
                    ],
                    ensure_ascii=False,
                ),
            )
        )
    if exchanges:
        latest = exchanges[-1]
        messages.append(latest[0])
        limit = TOOL_CHARS // max(1, len(latest) - 1)
        names = {call.call_id: call.name for call in latest[0].tool_calls}
        for message in latest[1:]:
            result = ToolResult.model_validate_json(message.content)
            # Preserve small source files in full for editing. Larger modules
            # still require a paged-read adapter from B.
            cap = 12000 if names.get(message.tool_call_id) == "file_read" else 4000
            messages.append(
                ChatMessage(
                    role="tool",
                    tool_call_id=message.tool_call_id,
                    content=tool_view(result, min(cap, limit)),
                )
            )
    if final:
        messages.append(ChatMessage(role="user", content=FINAL_INSTRUCTION))
    return messages
