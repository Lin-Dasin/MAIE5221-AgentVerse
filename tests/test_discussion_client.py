"""Client hardening and managed delivery must share one error/usage contract."""

import json

import httpx
import pytest

from protocol.models import ChatMessage
from runtime.client import ModelCallError, ModelOutputLimitError
from runtime.discussion_budget import DiscussionBudget
from tests.test_client import client_for
from tests.test_delivery_lifecycle import setup


@pytest.mark.parametrize("failure", ["length", "invalid"])
@pytest.mark.parametrize(
    "usage, expected",
    [
        ({"total_tokens": 4321}, 4321),
        ({"prompt_tokens": 20, "completion_tokens": 10}, 30),
        (None, None),
    ],
)
async def test_shared_budget_charges_client_errors_once(failure, usage, expected):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "length" if failure == "length" else "tool_calls",
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "incomplete",
                                    "function": {"name": "file_write", "arguments": '{"path":'},
                                }
                            ]
                        },
                    }
                ],
                "usage": usage,
            },
        )

    client = client_for(handler)
    budget = DiscussionBudget(client, 10000, lambda *_: 100)
    try:
        with pytest.raises(ModelCallError) as error:
            await budget.complete(
                [ChatMessage(role="user", content="Write a file")], [], max_tokens=50
            )
    finally:
        await client.aclose()
    assert isinstance(error.value, ModelOutputLimitError) == (failure == "length")
    assert error.value.usage_source == ("estimated" if expected is None else "provider")
    assert budget.tokens == error.value.tokens
    assert budget.remaining == 10000 - error.value.tokens
    assert budget.calls == len(requests) == 1
    if expected is not None:
        assert budget.tokens == expected
    else:
        assert budget.tokens > 50


async def test_http_truncation_recovers_handoff_without_executing_partial_tool(tmp_path):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            message = {
                "tool_calls": [
                    {
                        "id": "write",
                        "function": {
                            "name": "file_write",
                            "arguments": json.dumps({"path": "outputs/final.md", "text": "READY"}),
                        },
                    }
                ]
            }
            finish, tokens = "tool_calls", 1000
        elif len(requests) == 2:
            message = {
                "tool_calls": [
                    {
                        "id": "partial",
                        "function": {
                            "name": "file_write",
                            "arguments": '{"path":"outputs/unsafe.md","text":',
                        },
                    }
                ]
            }
            finish, tokens = "length", 600
        else:
            assert len(requests) == 3
            message = {"content": "Saved outputs/final.md; verify the actual file."}
            finish, tokens = "stop", 100
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": finish, "message": message}],
                "usage": {"total_tokens": tokens},
            },
        )

    runner, config, args = setup(tmp_path, max_steps_per_turn=2)
    client = client_for(handler)
    try:
        result = await runner.run(config, **args, model=client)
    finally:
        await client.aclose()
    assert result.status == "passed" and result.tokens == 1700
    assert result.model_calls == len(requests) == 3
    assert result.attempts[0].turns[0].tokens == result.tokens
    assert all("tools" not in request for request in requests[1:])
    assert requests[2]["messages"][-1]["content"] == "Previous report was cut off. Use one line."
    root = args["workspaces"]["writer"]
    assert (root / "outputs/final.md").read_text() == "READY"
    assert not (root / "outputs/unsafe.md").exists()
