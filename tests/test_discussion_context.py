"""Budget regression controls: shortened prompts, intact evidence, real local writes."""

import json

import pytest

from orchestrator.discussion import RoundRobinDiscussionEngine
from protocol.models import ChatMessage, ModelResponse, ToolCall, ToolResult
from runtime.discussion_context import (
    count_tokens,
    estimate_tokens,
    history_view,
    prompt_view,
    tool_view,
)
from sandbox.executor import RegisteredTool, SandboxToolExecutor
from tests.helpers import profile
from tests.test_discussion import HumanQueue, NoTools, ScriptedModel, discussion
from tools.fs import FILE_WRITE, write_file
from tools.web_search import WEB_SEARCH


def test_history_keeps_humans_and_recent_speaker_findings_without_mutation():
    instruction = "按原任务：选择近期具体事件，面向校内网站及社交媒体"
    history = [ChatMessage(role="user", content=instruction)]
    for index in range(12):
        history.append(
            ChatMessage(
                role="assistant",
                content=f"[writer] older version {index}: " + "discussion " * 2000,
            )
        )
    history.extend(
        [
            ChatMessage(role="assistant", content="[reviewer] The sources disagree on the date."),
            ChatMessage(
                role="assistant", content="[writer] outputs/article.md is ready for review."
            ),
            ChatMessage(
                role="assistant", content="[writer] outputs/article.md is ready for review."
            ),
        ]
    )
    snapshot = [item.model_dump() for item in history]
    view = json.loads(history_view(history))
    assert view["messages"][0] == {"role": "user", "content": instruction}
    assert (
        sum(len(row["content"]) for row in view["messages"] if row["role"] == "assistant") <= 4000
    )
    assert "sources disagree" in json.dumps(view)
    assert json.dumps(view).count("outputs/article.md") == 1
    assert view["omitted_assistant_messages"] > 0
    assert [item.model_dump() for item in history] == snapshot


@pytest.mark.parametrize("limit", [312, 2000])
def test_long_search_is_valid_json_with_exact_source_links(limit):
    url = "https://example.org/event"
    raw = ToolResult(
        ok=True,
        data={
            "query": '香港校园 AI "news"\n' * 1000,
            "count": 5,
            "results": [
                {"title": '"标题"\n' * 300, "url": url, "content": "evidence " * 1000}
                for _ in range(5)
            ],
        },
    )
    snapshot = raw.model_dump()
    text = tool_view(raw, limit)
    view = json.loads(text)
    assert len(text) <= limit
    assert view["ok"] and view["truncated_for_prompt"]
    assert view["data"]["count"] == 5
    assert view["data"]["results"][0]["url"] == url
    assert raw.model_dump() == snapshot


@pytest.mark.parametrize("limit", [312, 2000])
def test_long_failed_tool_keeps_exit_status_and_error(limit):
    raw = ToolResult(
        ok=False,
        error="pytest failed: " + '"error"\n' * 5000,
        data={
            "exit_code": 1,
            "truncated": True,
            "path": "outputs/report.md",
            "backend": "docker",
            "stderr": "失败\n" * 5000,
        },
    )
    text = tool_view(raw, limit)
    view = json.loads(text)
    assert len(text) <= limit
    assert not view["ok"] and view["error"].startswith("pytest failed")
    assert view["data"]["exit_code"] == 1 and view["data"]["truncated"]
    assert view["data"].get("path", "outputs/report.md") == "outputs/report.md"


def test_oversized_identifiers_are_omitted_instead_of_fabricated():
    path = "outputs/" + "x" * 5000 + ".md"
    url = "https://example.org/" + "y" * 5000
    for raw in (
        ToolResult(ok=True, data={"path": path, "text": "z" * 5000}),
        ToolResult(ok=True, data={"results": [{"url": url}], "count": 1}),
    ):
        view = tool_view(raw, 312)
        assert len(view) <= 312 and json.loads(view)["truncated_for_prompt"]
        data = json.loads(view)["data"]
        assert "path" not in data and not data.get("results")


def test_latest_tool_exchange_stays_paired_and_old_private_reasoning_is_removed():
    exchanges = []
    for index in range(3):
        calls = [ToolCall(call_id=f"{index}-{n}", name="web_search") for n in range(2)]
        exchanges.append(
            [
                ChatMessage(
                    role="assistant", tool_calls=calls, reasoning_content=f"private-{index}"
                ),
                *(
                    ChatMessage(
                        role="tool",
                        tool_call_id=call.call_id,
                        content=ToolResult(
                            ok=True, data={"text": "result " * 3000}
                        ).model_dump_json(),
                    )
                    for call in calls
                ),
            ]
        )
    snapshot = [[item.model_dump() for item in exchange] for exchange in exchanges]
    view = prompt_view(
        [ChatMessage(role="user", content="Current assignment")], exchanges, final=True
    )
    latest = next(item for item in view if item.role == "assistant")
    assert latest == exchanges[-1][0]
    assert [item.tool_call_id for item in view if item.role == "tool"] == ["2-0", "2-1"]
    assert "private-0" not in str(view) and "private-1" not in str(view)
    assert view[-1].content.startswith("Finish this contribution")
    assert [[item.model_dump() for item in exchange] for exchange in exchanges] == snapshot


def test_fallback_is_language_aware_but_custom_counter_can_replace_it():
    english = [ChatMessage(role="user", content="Verified evidence from campus news. " * 1000)]
    chinese = [ChatMessage(role="user", content="校园新闻" * 1000)]
    assert 0 < estimate_tokens(english, []) < len(english[0].content.encode("utf-8"))
    assert estimate_tokens(chinese, []) >= len(chinese[0].content.encode("utf-8"))
    assert count_tokens(lambda messages, tools: 123, english, []) == 123


@pytest.mark.parametrize("value", [-1, True, 1.5, None])
def test_invalid_injected_counter_is_rejected(value):
    with pytest.raises(ValueError, match="non-negative integer"):
        count_tokens(lambda messages, tools: value, [], [])


async def test_large_history_and_search_still_allow_real_artifact_with_default_budget(tmp_path):
    search_result = {
        "count": 1,
        "results": [
            {
                "url": "https://example.org/event",
                "title": "Campus event",
                "content": "Verified fixture evidence. " * 5000,
            }
        ],
    }
    executor = SandboxToolExecutor(
        tmp_path,
        {
            WEB_SEARCH.name: RegisteredTool(WEB_SEARCH, lambda *args: search_result, ("query",)),
            FILE_WRITE.name: RegisteredTool(FILE_WRITE, write_file, ("path", "text")),
        },
    )
    article = "# Campus event\n\nSource: https://example.org/event\n"
    model = ScriptedModel(
        ModelResponse(
            tokens=3000,
            tool_calls=[
                ToolCall(
                    call_id="search",
                    name="web_search",
                    arguments={"query": "campus event"},
                )
            ],
        ),
        ModelResponse(
            tokens=3000,
            tool_calls=[
                ToolCall(
                    call_id="write",
                    name="file_write",
                    arguments={"path": "outputs/article.md", "text": article},
                )
            ],
        ),
        ModelResponse(tokens=3000, content="Wrote outputs/article.md using the supplied source."),
    )
    human_text = "Use a specific event; report uncertainty honestly."
    goal = "Write outputs/article.md."
    result = await RoundRobinDiscussionEngine(
        executor.tool_definitions(),
        max_steps_per_turn=3,
    ).run(
        discussion(goal=goal, max_rounds=1),
        profiles={"writer": profile(tools=["web_search", "file_write"])},
        history=[
            ChatMessage(role="user", content=human_text),
            ChatMessage(role="assistant", content="[writer] earlier debate " * 10000),
        ],
        model=model,
        executor=executor,
        human=HumanQueue(),
        emit=None,
    )
    assert result.stopped_reason == "max_rounds"
    assert result.turns[0].tokens == 9000
    assert (executor.workspace("writer") / "outputs/article.md").read_text() == article
    assert result.turns[0].tool_results[0].data == search_result
    for messages, _, _ in model.requests:
        assert goal in messages[1].content and human_text in messages[1].content
        assert len(str(messages)) < 15000
    assert model.requests[-1][1] == []  # Last slot is a response, not another tool loop.


async def test_work_share_stops_tool_exploration_and_leaves_next_speaker_a_turn():
    class Search:
        async def execute(self, call, context):
            return ToolResult(ok=True, data={"count": 0, "results": []})

    model = ScriptedModel(
        ModelResponse(tokens=3000, tool_calls=[ToolCall(call_id="s", name="web_search")]),
        ModelResponse(tokens=500, content="No sources found; this remains unverified."),
        ModelResponse(tokens=500, content="I will record the uncertainty."),
    )
    result = await RoundRobinDiscussionEngine(
        [WEB_SEARCH],
        prompt_token_counter=lambda messages, tools: 1000 if tools else 500,
    ).run(
        discussion(speakers=["writer", "reviewer"], max_rounds=1, max_task_tokens=16000),
        profiles={"writer": profile(tools=["web_search"]), "reviewer": profile("reviewer")},
        history=[],
        model=model,
        executor=Search(),
        human=HumanQueue(),
        emit=None,
    )
    assert result.stopped_reason == "max_rounds"
    assert [turn.speaker for turn in result.turns] == ["writer", "reviewer"]
    assert model.requests[0][1] and not model.requests[1][1]
    assert "Research allowance has ended" in model.requests[1][0][-1].content
    assert sum(turn.tokens for turn in result.turns) == 4000


async def test_oversized_human_instruction_stops_instead_of_silently_discarding_it():
    model = ScriptedModel()
    result = await RoundRobinDiscussionEngine().run(
        discussion(),
        profiles={"writer": profile()},
        history=[ChatMessage(role="user", content="必须核实全部来源" * 10000)],
        model=model,
        executor=NoTools(),
        human=HumanQueue(),
        emit=None,
    )
    assert result.stopped_reason == "token_budget" and not model.requests


async def test_final_response_with_unoffered_tool_call_never_dispatches():
    model = ScriptedModel(
        ModelResponse(
            tokens=20,
            tool_calls=[ToolCall(call_id="write", name="file_write")],
        )
    )
    result = await RoundRobinDiscussionEngine([FILE_WRITE], max_steps_per_turn=1).run(
        discussion(),
        profiles={"writer": profile(tools=["file_write"])},
        history=[],
        model=model,
        executor=NoTools(),
        human=HumanQueue(),
        emit=None,
    )
    assert model.requests[0][1] == []
    assert result.stopped_reason == "step_limit" and not result.turns
