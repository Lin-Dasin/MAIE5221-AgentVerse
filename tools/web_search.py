"""Web search tool backed by Tavily.

The provider call is isolated in `fetch_tavily`, so switching to Serper or Brave
touches one function rather than the tool contract.

There is no key-less scraping fallback: without `SEARCH_API_KEY` the tool fails
with a readable message instead of returning an empty result list. An empty list
would let the model conclude "nothing exists" and write a confident wrong answer,
which is worse than a visible failure it can report as a blocker.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import httpx

from protocol.models import ExecutionContext, ToolDefinition
from sandbox.executor import ToolFailure

TAVILY_ENDPOINT = "https://api.tavily.com/search"
DEFAULT_MAX_RESULTS = 5
MAX_RESULTS_LIMIT = 10
DEFAULT_TIMEOUT_SEC = 20.0
MAX_CONTENT_CHARS = 1200


def _clip(text: str) -> str:
    if len(text) <= MAX_CONTENT_CHARS:
        return text
    return text[:MAX_CONTENT_CHARS] + "..."


def _max_results(arguments: dict[str, Any]) -> int:
    requested = arguments.get("max_results", DEFAULT_MAX_RESULTS)
    if isinstance(requested, bool) or not isinstance(requested, int):
        raise ValueError("max_results must be an integer")
    if not 1 <= requested <= MAX_RESULTS_LIMIT:
        raise ValueError(f"max_results must be between 1 and {MAX_RESULTS_LIMIT}")
    return requested


async def fetch_tavily(
    query: str,
    *,
    api_key: str,
    max_results: int,
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[dict[str, str]]:
    """Call Tavily and return normalized result rows.

    `transport` exists so tests can inject `httpx.MockTransport` and exercise the
    real parsing and error paths without touching the network.
    """
    payload = {
        "query": query,
        "max_results": max_results,
        "search_depth": "basic",
        "include_answer": False,
    }
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        async with httpx.AsyncClient(timeout=timeout_sec, transport=transport) as client:
            response = await client.post(TAVILY_ENDPOINT, json=payload, headers=headers)
    except httpx.TimeoutException as exc:
        raise ToolFailure(f"search request timed out after {timeout_sec:g}s") from exc
    except httpx.HTTPError as exc:
        raise ToolFailure(f"search request failed: {type(exc).__name__}") from exc

    if response.status_code != 200:
        raise ToolFailure(f"search request failed with HTTP {response.status_code}")

    try:
        body = response.json()
    except ValueError as exc:
        raise ToolFailure("search response was not valid JSON") from exc

    if not isinstance(body, dict) or not isinstance(body.get("results"), list):
        raise ToolFailure("unexpected search response: missing results list")

    rows: list[dict[str, str]] = []
    for item in body["results"]:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "title": str(item.get("title", "")),
                "url": str(item.get("url", "")),
                "content": _clip(str(item.get("content", ""))),
            }
        )
    return rows


async def run_search(
    arguments: dict[str, Any], workspace: Path, context: ExecutionContext
) -> dict[str, Any]:
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    query = query.strip()

    api_key = os.environ.get("SEARCH_API_KEY", "").strip()
    if not api_key:
        raise ToolFailure(
            "web_search is not configured: SEARCH_API_KEY is missing. "
            "Ask a team member for their own key and set it in your environment."
        )

    limit = _max_results(arguments)
    started = time.monotonic()
    rows = await fetch_tavily(query, api_key=api_key, max_results=limit)

    return {
        "query": query,
        "results": rows,
        "count": len(rows),
        "provider": "tavily",
        "duration_sec": round(time.monotonic() - started, 3),
    }


WEB_SEARCH = ToolDefinition(
    name="web_search",
    description=(
        "Search the web and return titles, URLs and content extracts. Use it for facts "
        "you cannot verify from the workspace. Cite the URL for every claim you keep."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query in natural language. Prefer specific terms.",
            },
            "max_results": {
                "type": "integer",
                "description": (
                    f"How many results to return. Default {DEFAULT_MAX_RESULTS}, "
                    f"maximum {MAX_RESULTS_LIMIT}."
                ),
                "minimum": 1,
                "maximum": MAX_RESULTS_LIMIT,
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
)
