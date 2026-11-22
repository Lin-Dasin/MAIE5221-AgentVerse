"""Check T1's per-query provenance without network access."""

from pathlib import Path

from harness.search_evidence import SOURCES
from harness.t1 import (
    GOAL_RULE,
    MAX_SEARCH_RESULTS,
    QUERIES,
    REPORT,
    SearchExecutor,
    SearchVerifier,
)
from protocol.models import ExecutionContext, RunConfig, ToolCall
from tools import web_search


async def test_goal_requires_the_correct_source_for_each_query(tmp_path: Path, monkeypatch) -> None:
    requests = []
    search_results = dict(zip(QUERIES, reversed(SOURCES), strict=True))

    async def fake_tavily(query, *, api_key, max_results):
        requests.append((query, max_results))
        url, fact = search_results[query]
        return [{"url": url, "title": "Official source", "content": fact}]

    monkeypatch.setenv("SEARCH_API_KEY", "test-key")
    monkeypatch.setattr(web_search, "fetch_tavily", fake_tavily)
    rows = ["| URL | Fact |", "| --- | --- |"]
    rows.extend(f"| {url} | {fact} |" for url, fact in SOURCES)
    (tmp_path / REPORT).write_text("\n".join(rows), encoding="utf-8")
    executor = SearchExecutor(tmp_path)
    context = ExecutionContext(
        config=RunConfig(), task_id="researcher-notes", agent_id="researcher", attempt=1
    )

    async def search(backend, query):
        return await backend.execute(
            ToolCall(
                call_id="search", name="web_search", arguments={"query": query, "max_results": 5}
            ),
            context,
        )

    for query in QUERIES:
        assert (await search(executor, query)).ok
    assert not (await search(executor, QUERIES[0])).ok
    assert requests == [(query, MAX_SEARCH_RESULTS) for query in QUERIES]

    swapped = await SearchVerifier(executor).check([GOAL_RULE], tmp_path)
    assert not swapped.passed
    assert (swapped.checks_passed, swapped.checks_total) == (2, 3)
    assert swapped.defects == ["search_evidence_missing: expected official URL for each query"]

    search_results = dict(zip(QUERIES, SOURCES, strict=True))
    executor = SearchExecutor(tmp_path)
    for query in QUERIES:
        assert (await search(executor, query)).ok
    matched = await SearchVerifier(executor).check([GOAL_RULE], tmp_path)
    assert matched.passed
    assert (matched.checks_passed, matched.checks_total) == (3, 3)
