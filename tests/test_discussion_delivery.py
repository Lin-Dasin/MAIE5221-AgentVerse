"""Action opportunities, bounded research and evidence-based coordination."""

import pytest

from orchestrator.discussion import RoundRobinDiscussionEngine
from protocol.models import ChatMessage, ModelResponse, ToolCall, ToolResult
from runtime.client import ModelOutputLimitError
from runtime.discussion_context import prompt_view
from sandbox.executor import RegisteredTool, SandboxToolExecutor
from tests.helpers import profile
from tests.test_discussion import HumanQueue, NoTools, ScriptedModel, discussion
from tools.fs import FILE_READ, FILE_WRITE, read_file, write_file
from tools.web_search import WEB_SEARCH


def call(name, number, **arguments):
    return ModelResponse(
        tokens=1000,
        tool_calls=[ToolCall(call_id=str(number), name=name, arguments=arguments)],
    )


async def test_budget_pressure_preserves_file_write_before_final_reply(tmp_path):
    executor = SandboxToolExecutor(
        tmp_path,
        {
            "file_write": RegisteredTool(FILE_WRITE, write_file, ("path", "text")),
        },
    )
    model = ScriptedModel(
        call("file_write", 1, path="outputs/draft.md", text="# Actual draft\nEvidence pending."),
        ModelResponse(tokens=500, content="Draft written, evidence remains provisional."),
        ModelResponse(tokens=100, content="I will review that draft."),
    )
    result = await RoundRobinDiscussionEngine(
        [FILE_WRITE, WEB_SEARCH],
        prompt_token_counter=lambda *_: 500,
    ).run(
        discussion(max_task_tokens=7000, max_rounds=1, speakers=["writer", "reviewer"]),
        profiles={
            "writer": profile(tools=["file_write", "web_search"]),
            "reviewer": profile("reviewer"),
        },
        history=[],
        model=model,
        executor=executor,
        human=HumanQueue(),
        emit=None,
    )
    assert result.stopped_reason == "max_rounds"
    assert [tool.name for tool in model.requests[0][1]] == ["file_write"]
    assert result.turns[0].tool_results[0].ok
    assert (executor.workspace("writer") / "outputs/draft.md").read_text().startswith("# Actual")


async def test_plan_only_reply_gets_one_bounded_action_opportunity(tmp_path):
    executor = SandboxToolExecutor(
        tmp_path,
        {
            "file_write": RegisteredTool(FILE_WRITE, write_file, ("path", "text")),
        },
    )
    model = ScriptedModel(
        ModelResponse(tokens=100, content="I will write the draft later."),
        call("file_write", 1, path="outputs/draft.md", text="Actual useful draft."),
        ModelResponse(tokens=100, content="Wrote outputs/draft.md."),
    )
    result = await RoundRobinDiscussionEngine([FILE_WRITE]).run(
        discussion(max_rounds=1),
        profiles={"writer": profile(tools=["file_write"])},
        history=[],
        model=model,
        executor=executor,
        human=HumanQueue(),
        emit=None,
    )
    assert result.stopped_reason == "max_rounds" and len(result.turns) == 1
    assert len(model.requests) == 3 and result.turns[0].tokens == 1200
    assert "No successful file write" in model.requests[1][0][-1].content
    assert "later" not in result.turns[0].content


async def test_search_quota_applies_to_batched_calls_without_disabling_file_tools():
    class Executor:
        def __init__(self):
            self.names = []

        async def execute(self, call, context):
            self.names.append(call.name)
            return ToolResult(ok=True, data={"path": "outputs/draft.md"})

    executor = Executor()
    model = ScriptedModel(
        ModelResponse(
            tokens=100,
            tool_calls=[
                ToolCall(call_id=str(index), name="web_search", arguments={"query": str(index)})
                for index in range(3)
            ],
        ),
        call("file_write", 4, path="outputs/draft.md", text="Findings and limitations"),
        ModelResponse(tokens=100, content="Draft ready."),
    )
    result = await RoundRobinDiscussionEngine([WEB_SEARCH, FILE_WRITE]).run(
        discussion(max_rounds=1),
        profiles={"writer": profile(tools=["web_search", "file_write"])},
        history=[],
        model=model,
        executor=executor,
        human=HumanQueue(),
        emit=None,
    )
    assert result.stopped_reason == "max_rounds"
    assert executor.names == ["web_search", "web_search", "file_write"]
    assert not result.turns[0].tool_results[2].ok
    assert [tool.name for tool in model.requests[1][1]] == ["file_write"]


async def test_emit_adapter_can_handoff_candidate_before_next_persona(tmp_path):
    executor = SandboxToolExecutor(
        tmp_path,
        {
            "file_write": RegisteredTool(FILE_WRITE, write_file, ("path", "text")),
            "file_read": RegisteredTool(FILE_READ, read_file, ("path",)),
        },
    )
    model = ScriptedModel(
        call("file_write", 1, path="outputs/draft.md", text="Specific draft to inspect."),
        ModelResponse(tokens=100, content="Please review outputs/draft.md."),
        call("file_read", 2, path="inputs/shared/writer/draft.md"),
        ModelResponse(tokens=100, content="Inspected the real draft; sourcing is still pending."),
    )

    async def adapter(turn):
        # D's adapter owns copies. A only awaits emit; it never shares filesystem roots.
        if turn.speaker == "writer":
            destination = executor.workspace("reviewer") / "inputs/shared/writer/draft.md"
            destination.parent.mkdir(parents=True)
            destination.write_bytes(
                (executor.workspace("writer") / "outputs/draft.md").read_bytes()
            )

    result = await RoundRobinDiscussionEngine([FILE_READ, FILE_WRITE]).run(
        discussion(max_rounds=1, speakers=["writer", "reviewer"]),
        profiles={
            "writer": profile(tools=["file_write"]),
            "reviewer": profile("reviewer", ["file_read"]),
        },
        history=[],
        model=model,
        executor=executor,
        human=HumanQueue(),
        emit=adapter,
    )
    assert result.stopped_reason == "max_rounds"
    assert result.turns[1].tool_results[0].data["text"] == "Specific draft to inspect."
    assert "candidate; quality not verified" in model.requests[2][0][1].content


@pytest.mark.parametrize("limit", [-1, 17, True, 1.5])
def test_search_limit_configuration_is_validated(limit):
    with pytest.raises(ValueError):
        RoundRobinDiscussionEngine(max_search_calls_per_turn=limit)


def test_source_read_preserves_middle_of_small_module_for_editing():
    source = "# beginning\n" + "x = 1\n" * 600 + "def cancel(): pass\n" + "y = 2\n" * 600
    raw = ToolResult(ok=True, data={"path": "inputs/shared/dev/app.py", "text": source})
    exchange = [
        ChatMessage(role="assistant", tool_calls=[ToolCall(call_id="read", name="file_read")]),
        ChatMessage(role="tool", tool_call_id="read", content=raw.model_dump_json()),
    ]
    view = prompt_view([], [exchange], final=False)
    assert ToolResult.model_validate_json(view[-1].content).data["text"] == source


async def test_truncated_output_gets_one_retry_and_its_usage_is_charged():
    model = ScriptedModel(
        ModelOutputLimitError(4000),
        ModelResponse(tokens=100, content="Useful short deliverable."),
    )
    result = await RoundRobinDiscussionEngine().run(
        discussion(max_rounds=1),
        profiles={"writer": profile()},
        history=[],
        model=model,
        executor=NoTools(),
        human=HumanQueue(),
        emit=None,
    )
    assert result.stopped_reason == "max_rounds" and result.turns[0].tokens == 4100
    assert len(model.requests) == 2
    assert "none of its tool calls ran" in model.requests[1][0][-1].content


async def test_delivery_reminder_does_not_deny_prior_code_execution():
    class Executor:
        async def execute(self, call, context):
            return ToolResult(ok=True, data={"exit_code": 0})

    from tools.code_exec import CODE_EXEC

    model = ScriptedModel(
        call("code_exec", 1, code="print('done')"),
        ModelResponse(tokens=100, content="I executed code; there are no files yet."),
        ModelResponse(tokens=100, content="The work remains partial."),
    )
    result = await RoundRobinDiscussionEngine([FILE_WRITE, CODE_EXEC]).run(
        discussion(max_rounds=1),
        profiles={"writer": profile(tools=["file_write", "code_exec"])},
        history=[],
        model=model,
        executor=Executor(),
        human=HumanQueue(),
        emit=None,
    )
    assert result.stopped_reason == "max_rounds" and len(model.requests) == 3
    assert "If code_exec already created files" in model.requests[2][0][-1].content
    assert result.turns[0].tool_results[0].data["exit_code"] == 0


@pytest.mark.parametrize(
    "budget, expected_calls, reason",
    [
        (60000, 2, "model_error"),
        (5000, 1, "token_budget"),
    ],
)
async def test_length_repair_is_bounded_by_retries_and_task_budget(budget, expected_calls, reason):
    model = ScriptedModel(ModelOutputLimitError(5000), ModelOutputLimitError(5000))
    result = await RoundRobinDiscussionEngine(prompt_token_counter=lambda *_: 500).run(
        discussion(max_task_tokens=budget),
        profiles={"writer": profile()},
        history=[],
        model=model,
        executor=NoTools(),
        human=HumanQueue(),
        emit=None,
    )
    assert result.stopped_reason == reason and len(model.requests) == expected_calls


async def test_repeated_listings_reuse_evidence_until_even_a_failed_mutation():
    from tools.code_exec import CODE_EXEC
    from tools.fs import FILE_LIST

    class Executor:
        def __init__(self):
            self.calls = []

        async def execute(self, call, context):
            self.calls.append(call.name)
            if call.name == "code_exec":
                return ToolResult(ok=False, error="failed after a possible partial write")
            return ToolResult(ok=True, data={"entries": [str(len(self.calls))]})

    executor = Executor()
    requests = [
        ToolCall(call_id="one", name="file_list", arguments={"path": "outputs"}),
        ToolCall(call_id="two", name="file_list", arguments={"path": "outputs"}),
        ToolCall(call_id="three", name="code_exec"),
        ToolCall(call_id="four", name="file_list", arguments={"path": "outputs"}),
    ]
    result = await RoundRobinDiscussionEngine([FILE_LIST, CODE_EXEC]).run(
        discussion(max_rounds=1),
        profiles={"writer": profile(tools=["file_list", "code_exec"])},
        history=[],
        model=ScriptedModel(
            ModelResponse(tool_calls=requests, tokens=100),
            ModelResponse(content="Listed actual files.", tokens=100),
        ),
        executor=executor,
        human=HumanQueue(),
        emit=None,
    )
    assert executor.calls == ["file_list", "code_exec", "file_list"]
    assert len(result.turns[0].tool_calls) == 4
    results = result.turns[0].tool_results
    assert results[1].meta["reused_listing"] and results[0].data == results[1].data
    assert results[3].data != results[1].data
