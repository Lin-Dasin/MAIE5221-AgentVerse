"""Tests for the web search tool.

No test touches the network: provider calls either go through
``httpx.MockTransport`` or are replaced by a stub on the module.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from protocol.models import ExecutionContext, RunConfig, ToolCall, ToolResult
from sandbox.executor import SandboxToolExecutor, ToolFailure
from tools import web_search
from tools.registry import build


def make_context(agent_id: str = "researcher") -> ExecutionContext:
    return ExecutionContext(config=RunConfig(), task_id="t1", agent_id=agent_id, attempt=1)


def make_executor(root: Path) -> SandboxToolExecutor:
    return SandboxToolExecutor(root, build())


async def call(
    executor: SandboxToolExecutor, name: str, **arguments: object
) -> ToolResult:
    return await executor.execute(
        ToolCall(call_id="c1", name=name, arguments=arguments), make_context()
    )


async def test_missing_key_fails_with_a_readable_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SEARCH_API_KEY", raising=False)

    result = await call(make_executor(tmp_path), "web_search", query="python asyncio")

    assert result.ok is False
    assert "SEARCH_API_KEY is missing" in (result.error or "")


async def test_successful_search_is_shaped_for_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEARCH_API_KEY", "test-key")

    async def fake_fetch(
        query: str, *, api_key: str, max_results: int
    ) -> list[dict[str, str]]:
        assert api_key == "test-key"
        assert max_results == 2
        return [{"title": "Async IO", "url": "https://example.test/a", "content": "text"}]

    monkeypatch.setattr(web_search, "fetch_tavily", fake_fetch)

    result = await call(make_executor(tmp_path), "web_search", query="asyncio", max_results=2)

    assert result.ok is True
    assert result.data["count"] == 1
    assert result.data["results"][0]["url"] == "https://example.test/a"
    assert result.data["provider"] == "tavily"


async def test_missing_query_is_rejected_before_any_request(tmp_path: Path) -> None:
    result = await call(make_executor(tmp_path), "web_search")

    assert result.ok is False
    assert "missing required argument" in (result.error or "")


async def test_max_results_out_of_range_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEARCH_API_KEY", "test-key")

    result = await call(make_executor(tmp_path), "web_search", query="x", max_results=99)

    assert result.ok is False
    assert "invalid argument" in (result.error or "")


async def test_http_error_becomes_a_tool_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"detail": "rate limited"})

    with pytest.raises(ToolFailure, match="HTTP 429"):
        await web_search.fetch_tavily(
            "q", api_key="k", max_results=1, transport=httpx.MockTransport(handler)
        )


async def test_timeout_becomes_a_tool_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    with pytest.raises(ToolFailure, match="timed out"):
        await web_search.fetch_tavily(
            "q", api_key="k", max_results=1, transport=httpx.MockTransport(handler)
        )


async def test_invalid_json_becomes_a_tool_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>")

    with pytest.raises(ToolFailure, match="not valid JSON"):
        await web_search.fetch_tavily(
            "q", api_key="k", max_results=1, transport=httpx.MockTransport(handler)
        )


async def test_long_content_is_clipped() -> None:
    long_text = "x" * (web_search.MAX_CONTENT_CHARS + 500)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"results": [{"title": "t", "url": "u", "content": long_text}]},
        )

    rows = await web_search.fetch_tavily(
        "q", api_key="k", max_results=1, transport=httpx.MockTransport(handler)
    )

    assert len(rows[0]["content"]) <= web_search.MAX_CONTENT_CHARS + 3
