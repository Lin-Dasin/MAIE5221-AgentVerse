"""Exercise the live entry point with offline HTTP and real file/trace adapters."""

import json
from collections import deque

import httpx

from runtime.client import DeepSeekClient
from scripts import live_smoke
from tests.helpers import graph, task


async def test_shared_client_plan_to_real_archive_and_goal_verdict(tmp_path, monkeypatch, capsys):
    planned = graph(task("notes"))
    responses = deque(
        [
            {"content": "OK"},
            {"content": planned.model_dump_json()},
            {"content": '{"task_ids":[],"rationale":"No search required"}'},
            {"content": '{"task_ids":["notes"],"rationale":"Can write Markdown"}'},
            {"content": planned.model_dump_json()},
            {
                "content": None,
                "tool_calls": [
                    {
                        "id": "write-notes",
                        "type": "function",
                        "function": {
                            "name": "file_write",
                            "arguments": json.dumps(
                                {
                                    "path": "outputs/notes.md",
                                    "text": "# Local smoke test\nSynthetic fixture",
                                }
                            ),
                        },
                    }
                ],
            },
            {"content": "File written"},
        ]
    )
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": responses.popleft()}],
                "usage": {"total_tokens": 5},
            },
        )

    client = DeepSeekClient(
        base_url="https://api.deepseek.com",
        api_key="synthetic-test-key",
        model="synthetic-model",
        transport=httpx.MockTransport(handler),
    )
    monkeypatch.setattr(DeepSeekClient, "from_environment", classmethod(lambda cls: client))
    status = await live_smoke.run(
        "Write a local Markdown note without search", tmp_path, 8, 4096, 60000
    )
    assert status == 0 and not responses and client.http.is_closed
    assert all(request["thinking"] == {"type": "disabled"} for request in requests)
    archived = list((tmp_path / "artifacts").glob("*/notes/notes.md"))
    assert len(archived) == 1 and "Synthetic fixture" in archived[0].read_text()
    assert "goal verdict: PASS" in capsys.readouterr().out
    saved_graph = json.loads(next(tmp_path.glob("graph-*.json")).read_text())
    assert saved_graph["goal_acceptance"][0]["path"] == "notes.md"
    assert (tmp_path / live_smoke.TRACE_FILENAME).is_file()


async def test_probe_error_closes_shared_client_without_printing_response_body(
    tmp_path,
    monkeypatch,
    capsys,
):
    client = DeepSeekClient(
        base_url="https://api.deepseek.com",
        api_key="synthetic-test-key",
        model="synthetic-model",
        transport=httpx.MockTransport(lambda _: httpx.Response(401, text="private-error-body")),
    )
    monkeypatch.setattr(DeepSeekClient, "from_environment", classmethod(lambda cls: client))
    assert await live_smoke.run("Write a note", tmp_path, 8, 4096, 60000) == 2
    assert client.http.is_closed
    output = capsys.readouterr()
    assert "HTTP 401" in output.err and "private-error-body" not in output.err
    assert not (tmp_path / "artifacts").exists()
