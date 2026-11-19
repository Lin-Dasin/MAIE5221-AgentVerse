"""Offline HTTP-contract regressions; no requests reach a model provider."""

import json

import httpx
import pytest

from orchestrator.approvals import PolicyApprover
from orchestrator.scheduler import Scheduler
from protocol.models import ChatMessage, TaskStatus
from runtime.agent import AgentRuntime
from runtime.client import DeepSeekClient, ModelCallError
from tests.helpers import FixtureTools, StubVerifier, config, graph, profile, task
from tests.test_runtime import write_tool


def client_for(handler, **kwargs):
    return DeepSeekClient(
        base_url="https://api.deepseek.com",
        api_key="synthetic-test-key",
        model="synthetic-model",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


@pytest.mark.parametrize("thinking", ["disabled", "enabled"])
async def test_reasoning_survives_tool_approval_and_multi_turn_replay(tmp_path, thinking):
    requests = []

    async def handler(request):
        requests.append(json.loads(request.content))
        message = {"content": "Done", "reasoning_content": "synthetic final reasoning"}
        if len(requests) == 1:
            message = {
                "content": None,
                "reasoning_content": "synthetic tool reasoning",
                "tool_calls": [
                    {
                        "id": "write1",
                        "type": "function",
                        "function": {
                            "name": "file_write",
                            "arguments": json.dumps(
                                {
                                    "path": "outputs/t1.md",
                                    "text": "synthetic fixture",
                                }
                            ),
                        },
                    }
                ],
            }
        return httpx.Response(
            200,
            json={
                "choices": [{"message": message}],
                "usage": {"total_tokens": 12},
            },
        )

    client = client_for(handler, thinking=thinking)
    executor = FixtureTools(tmp_path)
    runtime = AgentRuntime(
        client,
        executor,
        {"writer": profile(tools=["file_write"])},
        [write_tool(approval=True)],
    )
    try:
        result = await Scheduler(
            runtime,
            StubVerifier(),
            PolicyApprover(frozenset({"tool:file_write"})),
            root=tmp_path,
        ).run(graph(task()), config())
    finally:
        await client.aclose()
    assert result.tasks[0].status == TaskStatus.SUCCESS
    assert result.tasks[0].tokens == 24
    assert len(executor.calls) == 1 and len(requests) == 2
    assert len(result.tasks[0].hitl_decisions) == 1
    assert all(r["thinking"] == {"type": thinking} for r in requests)
    assistant = next(m for m in requests[1]["messages"] if m["role"] == "assistant")
    if thinking == "enabled":
        assert assistant["reasoning_content"] == "synthetic tool reasoning"
    else:
        assert "reasoning_content" not in assistant
    assert requests[1]["messages"][-1]["tool_call_id"] == "write1"
    assert runtime.sessions == {}


@pytest.mark.parametrize("mode", [None, "enabled", "disabled"])
async def test_environment_selects_validated_thinking_mode(monkeypatch, mode):
    for key, value in {
        "LLM_BASE_URL": "https://api.deepseek.com",
        "LLM_API_KEY": "synthetic-test-key",
        "LLM_MODEL": "synthetic-model",
    }.items():
        monkeypatch.setenv(key, value)
    if mode is None:
        monkeypatch.delenv("LLM_THINKING", raising=False)
    else:
        monkeypatch.setenv("LLM_THINKING", mode)
    client = DeepSeekClient.from_environment()
    try:
        assert client.thinking == (mode or "disabled")
    finally:
        await client.aclose()
    monkeypatch.setenv("LLM_THINKING", "automatic")
    with pytest.raises(ValueError, match="LLM_THINKING"):
        DeepSeekClient.from_environment()


@pytest.mark.parametrize("failure", [429, 503, "timeout"])
async def test_thinking_request_retries_are_bounded_and_preserve_payload(monkeypatch, failure):
    requests = []

    async def no_delay(_):
        pass

    monkeypatch.setattr("runtime.client.asyncio.sleep", no_delay)

    async def handler(request):
        requests.append(json.loads(request.content))
        if failure == "timeout":
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        return httpx.Response(failure, text="sensitive body must not be reported")

    client = client_for(handler, thinking="enabled", max_retries=2)
    try:
        with pytest.raises(ModelCallError, match="bounded retries") as error:
            await client.complete([ChatMessage(role="user", content="test")], [], max_tokens=50)
    finally:
        await client.aclose()
    assert len(requests) == 3 and requests[0] == requests[1] == requests[2]
    assert requests[0]["thinking"] == {"type": "enabled"}
    assert "sensitive" not in str(error.value)


@pytest.mark.parametrize(
    "message",
    [
        {"content": "test", "reasoning_content": {"invalid": "not a string"}},
        {
            "content": None,
            "tool_calls": [
                {
                    "id": "bad",
                    "function": {"name": "file_write", "arguments": "not json"},
                }
            ],
        },
    ],
)
async def test_invalid_provider_envelope_is_a_model_error(message):
    client = client_for(lambda _: httpx.Response(200, json={"choices": [{"message": message}]}))
    try:
        with pytest.raises(ModelCallError, match="invalid response envelope"):
            await client.complete([ChatMessage(role="user", content="test")], [], max_tokens=50)
    finally:
        await client.aclose()
