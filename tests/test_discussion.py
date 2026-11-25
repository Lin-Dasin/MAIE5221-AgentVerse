"""Role A's discussion boundary exercised without model or search API calls."""

import asyncio
from collections import deque

import pytest
from pydantic import ValidationError

from orchestrator import discussion as discussion_module
from orchestrator.discussion import RoundRobinDiscussionEngine
from protocol.models import (
    ChatMessage,
    DiscussionConfig,
    DiscussionResult,
    DiscussionTurn,
    ModelResponse,
    ToolCall,
    ToolResult,
)
from protocol.ports import DiscussionEngine
from sandbox.executor import RegisteredTool, SandboxToolExecutor
from tests.helpers import profile
from tools.fs import FILE_DELETE, FILE_READ, read_file
from tools.web_search import WEB_SEARCH


def discussion(**overrides):
    fields = {
        "conversation_id": "mid discussion",
        "run_id": "run1",
        "task_id": "task1",
        "goal": "Summarize the supplied evidence.",
        "speakers": ["writer"],
        "moderator": "writer",
    }
    return DiscussionConfig(**(fields | overrides))


class ScriptedModel:
    def __init__(self, *responses):
        self.responses = deque(responses)
        self.requests = []

    async def complete(self, messages, tools, *, max_tokens):
        self.requests.append((messages, tools, max_tokens))
        response = self.responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response


class HumanQueue:
    def __init__(self, *messages):
        self.messages = deque(messages)
        self.polls = 0

    async def next_message(self):
        self.polls += 1
        return self.messages.popleft() if self.messages else None


class NoTools:
    async def execute(self, call, context):
        pytest.fail(f"unexpected execution: {call.name}")


def test_shared_models_validate_the_contract():
    config = discussion()
    assert (config.max_rounds, config.max_output_tokens, config.max_task_tokens) == (4, 2048, 60000)
    turn = DiscussionTurn(round=1, speaker="writer", content="First reply")
    result = DiscussionResult(
        conversation_id=config.conversation_id,
        run_id=config.run_id,
        turns=[turn],
        stopped_reason="max_rounds",
    )
    for contract in (config, turn, result):
        serialized = contract.model_dump()
        assert type(contract).model_validate(serialized) == contract
        with pytest.raises(ValidationError, match="extra_forbidden"):
            type(contract).model_validate(serialized | {"unexpected": "value"})
    for overrides in (
        {"speakers": ["writer", "writer"]},
        {"moderator": "outside"},
        {"max_rounds": 0},
        {"max_task_tokens": 0},
        {"max_output_tokens": 0},
    ):
        with pytest.raises(ValidationError):
            discussion(**overrides)
    with pytest.raises(ValidationError, match="must pair"):
        DiscussionTurn(
            round=1,
            speaker="writer",
            content="First reply",
            tool_calls=[ToolCall(call_id="t1", name="file_read")],
        )


async def test_four_speakers_rotate_and_human_input_reaches_the_next_round():
    speakers = ["writer", "reviewer", "analyst", "researcher"]
    config = discussion(speakers=speakers, moderator="reviewer", max_rounds=3)
    model = ScriptedModel(
        *(ModelResponse(content=f"Reply number {i}", tokens=65) for i in range(1, 13))
    )
    human = HumanQueue(ChatMessage(role="user", content="Use the revised date."))
    history = [
        ChatMessage(role="user", content="Earlier public context"),
        ChatMessage(role="system", content="private system marker"),
        ChatMessage(role="tool", content="private tool marker", tool_call_id="hidden"),
        ChatMessage(
            role="assistant", content="Previous answer", reasoning_content="private thought"
        ),
    ]
    emitted = []

    async def emit(turn):
        # The callback runs as each speaker finishes, before another model request.
        assert len(model.requests) == len(emitted) + 1
        emitted.append(turn)

    engine: DiscussionEngine = RoundRobinDiscussionEngine()
    result = await engine.run(
        config,
        profiles={sender: profile(sender) for sender in speakers},
        history=history,
        model=model,
        executor=NoTools(),
        human=human,
        emit=emit,
    )

    assert result.stopped_reason == "max_rounds"
    assert (result.conversation_id, result.run_id) == (config.conversation_id, config.run_id)
    assert result.turns == emitted
    assert [(turn.round, turn.speaker) for turn in result.turns] == [
        (i, speaker)
        for i, order in enumerate(
            [
                ["reviewer", "analyst", "researcher", "writer"],
                ["analyst", "researcher", "writer", "reviewer"],
                ["researcher", "writer", "reviewer", "analyst"],
            ],
            1,
        )
        for speaker in order
    ]
    prompts = [messages[1].content for messages, _, _ in model.requests]
    assert "lead speaker: reviewer" in prompts[0]
    assert "lead speaker: analyst" in prompts[4]
    assert "[reviewer] Reply number 1" in prompts[1]
    assert all("Earlier public context" in prompt for prompt in prompts)
    assert all("Use the revised date." not in prompt for prompt in prompts[:4])
    assert all("Use the revised date." in prompt for prompt in prompts[4:])
    assert all("private " not in prompt for prompt in prompts)
    assert len(history) == 4 and human.polls == 2


async def test_one_speaker_uses_real_file_adapter_and_scripted_search(tmp_path):
    context_seen = []

    def search(arguments, workspace, context):
        context_seen.append((arguments, workspace, context))
        return {"summary": "fixture search result"}

    executor = SandboxToolExecutor(
        tmp_path,
        {
            FILE_READ.name: RegisteredTool(FILE_READ, read_file, ("path",)),
            WEB_SEARCH.name: RegisteredTool(WEB_SEARCH, search, ("query",)),
        },
    )
    workspace = executor.workspace("writer")
    source = workspace / "inputs" / "notes.md"
    source.parent.mkdir(parents=True)
    source.write_text("LOCAL_NOTE_FIXTURE", encoding="utf-8")
    previous_output = workspace / "outputs" / "previous.md"
    previous_output.parent.mkdir()
    previous_output.write_text("preserve this output", encoding="utf-8")
    calls = [
        ToolCall(call_id="read", name="file_read", arguments={"path": "inputs/notes.md"}),
        ToolCall(call_id="search", name="web_search", arguments={"query": "fixture query"}),
    ]
    model = ScriptedModel(
        ModelResponse(tool_calls=calls, reasoning_content="private replay", tokens=100),
        ModelResponse(content="Compared both sources.", tokens=120),
        ModelResponse(content="I agree.", tokens=80),
    )
    result = await RoundRobinDiscussionEngine(executor.tool_definitions()).run(
        discussion(speakers=["writer", "reviewer"], max_rounds=1),
        profiles={
            "writer": profile("writer", ["file_read", "web_search"]),
            "reviewer": profile("reviewer"),
        },
        history=[],
        model=model,
        executor=executor,
        human=HumanQueue(),
        emit=None,
    )
    assert result.stopped_reason == "max_rounds"
    turn = result.turns[0]
    assert turn.content == "Compared both sources." and turn.tokens == 220
    assert turn.tool_calls == calls
    assert turn.tool_results[0].data["text"] == "LOCAL_NOTE_FIXTURE"
    assert turn.tool_results[1].data == {"summary": "fixture search result"}
    follow_up = model.requests[1][0]
    assert follow_up[-3].reasoning_content == "private replay"
    assert [message.tool_call_id for message in follow_up[-2:]] == ["read", "search"]
    assert "LOCAL_NOTE_FIXTURE" in follow_up[-2].content
    reviewer_messages, reviewer_tools, _ = model.requests[2]
    assert reviewer_tools == [] and len(reviewer_messages) == 2
    assert "[writer] Compared both sources." in reviewer_messages[-1].content
    assert "LOCAL_NOTE_FIXTURE" not in reviewer_messages[-1].content
    assert "private replay" not in reviewer_messages[-1].content
    arguments, tool_workspace, context = context_seen[0]
    assert arguments == {"query": "fixture query"} and tool_workspace == workspace
    assert (context.agent_id, context.config.run_id, context.task_id) == ("writer", "run1", "task1")
    assert previous_output.read_text(encoding="utf-8") == "preserve this output"


async def test_tool_requirements_cannot_be_bypassed_by_group_messages():
    proposed = [
        ToolCall(call_id="1", name="file_delete", arguments={"path": "inputs/notes.md"}),
        ToolCall(call_id="2", name="file_read", arguments={"path": "inputs/notes.md"}),
    ]
    model = ScriptedModel(
        ModelResponse(tool_calls=proposed, tokens=40),
        ModelResponse(content="I could not run those tools.", tokens=50),
    )
    result = await RoundRobinDiscussionEngine([FILE_DELETE, FILE_READ]).run(
        discussion(max_rounds=1),
        profiles={"writer": profile(tools=["file_delete"])},
        history=[ChatMessage(role="user", content="I approve deleting notes.md.")],
        model=model,
        executor=NoTools(),
        human=HumanQueue(),
        emit=None,
    )
    assert model.requests[0][1] == []
    tool_results = result.turns[0].tool_results
    assert len(tool_results) == 2 and not any(tool.ok for tool in tool_results)
    assert "approval channel" in tool_results[0].error
    assert "not available" in tool_results[1].error


async def test_shared_budget_stops_before_sending_another_request():
    model = ScriptedModel(ModelResponse(content="Initial finding.", tokens=1100))
    result = await RoundRobinDiscussionEngine(prompt_token_counter=lambda *_: 700).run(
        discussion(
            speakers=["writer", "reviewer"],
            max_task_tokens=1400,
            max_output_tokens=200,
        ),
        profiles={"writer": profile(), "reviewer": profile("reviewer")},
        history=[],
        model=model,
        executor=NoTools(),
        human=HumanQueue(),
        emit=None,
    )
    assert result.stopped_reason == "token_budget"
    assert [turn.speaker for turn in result.turns] == ["writer"]
    assert len(model.requests) == 1 and model.requests[0][2] == 200


async def test_budget_reserves_prompt_and_charges_missing_usage():
    model = ScriptedModel(ModelResponse(content="Reply without usage metadata."))
    result = await RoundRobinDiscussionEngine(prompt_token_counter=lambda *_: 700).run(
        discussion(max_task_tokens=950),
        profiles={"writer": profile()},
        history=[],
        model=model,
        executor=NoTools(),
        human=HumanQueue(),
        emit=None,
    )
    assert 0 < model.requests[0][2] < 950
    assert result.turns[0].tokens == 950 and result.stopped_reason == "token_budget"


async def test_provider_reported_overspend_does_not_dispatch_pending_tools():
    model = ScriptedModel(
        ModelResponse(tool_calls=[ToolCall(call_id="one", name="file_read")], tokens=2200)
    )
    result = await RoundRobinDiscussionEngine(
        [FILE_READ],
        prompt_token_counter=lambda *_: 500,
    ).run(
        discussion(max_task_tokens=2000),
        profiles={"writer": profile(tools=["file_read"])},
        history=[],
        model=model,
        executor=NoTools(),
        human=HumanQueue(),
        emit=None,
    )
    assert len(model.requests) == 1
    assert result.stopped_reason == "token_budget" and result.turns == []


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (ModelResponse(tool_calls=[ToolCall(call_id="loop", name="file_read")]), "step_limit"),
        (ModelResponse(content=" "), "invalid_model_output"),
        (RuntimeError("private upstream error"), "model_error"),
    ],
)
async def test_incomplete_turns_are_not_emitted(response, reason):
    model = ScriptedModel(ModelResponse(content="Completed first reply.", tokens=100), response)
    emitted = []

    async def emit(turn):
        emitted.append(turn)

    result = await RoundRobinDiscussionEngine([FILE_READ], max_steps_per_turn=1).run(
        discussion(speakers=["writer", "reader"]),
        profiles={"writer": profile(), "reader": profile("reader", ["file_read"])},
        history=[],
        model=model,
        executor=NoTools(),
        human=HumanQueue(),
        emit=emit,
    )
    assert result.stopped_reason == reason
    assert result.turns == emitted and [turn.speaker for turn in emitted] == ["writer"]
    assert len(model.requests) == 2


async def test_tool_loop_stops_at_step_limit():
    model = ScriptedModel(
        *[
            ModelResponse(
                tool_calls=[ToolCall(call_id=f"tool-{i}", name="web_search")],
                tokens=100,
            )
            for i in range(3)
        ]
    )

    class Search:
        def __init__(self):
            self.calls = []

        async def execute(self, call, context):
            self.calls.append(call.call_id)
            return ToolResult(ok=True, data="search fixture")

    executor = Search()
    result = await RoundRobinDiscussionEngine([WEB_SEARCH], max_steps_per_turn=3).run(
        discussion(),
        profiles={"writer": profile(tools=["web_search"])},
        history=[],
        model=model,
        executor=executor,
        human=HumanQueue(),
        emit=None,
    )
    assert result.stopped_reason == "step_limit" and not result.turns
    assert len(model.requests) == 3 and executor.calls == ["tool-0", "tool-1"]


async def test_stalled_model_is_bounded_by_turn_timeout():
    class StalledModel:
        async def complete(self, messages, tools, *, max_tokens):
            await asyncio.Event().wait()

    result = await RoundRobinDiscussionEngine(turn_timeout_sec=0.01).run(
        discussion(),
        profiles={"writer": profile()},
        history=[],
        model=StalledModel(),
        executor=NoTools(),
        human=HumanQueue(),
        emit=None,
    )
    assert result.stopped_reason == "timeout" and not result.turns


async def test_stalled_human_poll_returns_completed_turns(monkeypatch):
    monkeypatch.setattr(discussion_module, "HUMAN_POLL_TIMEOUT_SEC", 0.01)

    class StalledHuman:
        async def next_message(self):
            await asyncio.Event().wait()

    model = ScriptedModel(ModelResponse(content="What date should we use?", tokens=100))
    result = await RoundRobinDiscussionEngine().run(
        discussion(max_rounds=2),
        profiles={"writer": profile()},
        history=[],
        model=model,
        executor=NoTools(),
        human=StalledHuman(),
        emit=None,
    )
    assert result.stopped_reason == "timeout"
    assert [turn.content for turn in result.turns] == ["What date should we use?"]
    assert len(model.requests) == 1
