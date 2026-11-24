"""Exercise actual JSONL memory and model-client wire requests without a live key."""

import json
import re

import httpx
import pytest

from orchestrator.approvals import PolicyApprover
from orchestrator.bootstrap import build_service
from runtime.client import DeepSeekClient, ModelCallError
from scripts.worker_memory_demo import run_pair
from tests.helpers import (
    FakeMemory,
    FixtureTools,
    ScriptedModel,
    StubVerifier,
    config,
    graph,
    profile,
    task,
)
from tests.test_runtime import write_response, write_tool


@pytest.mark.parametrize("enabled", [True, False])
async def test_chinese_worker_memory_with_http_wire_and_real_adapters(tmp_path, enabled):
    payloads = []

    async def handler(request):
        payload = json.loads(request.content)
        payloads.append(payload)
        messages = payload["messages"]
        prompt = json.loads(next(msg["content"] for msg in messages if msg["role"] == "user"))
        marker = next((word for word in prompt["task"]["title"].split("：") if "MEM-" in word), "")
        if prompt["task"]["task_id"] == "recall":
            assert "MEM-" not in prompt["task"]["title"]
            assert ("MEM-" in prompt["memory_context"]) is enabled
        source = marker or prompt["memory_context"]
        match = re.search(r"MEM-[a-f0-9]{16}", source)
        message = {"content": "完成核对发票。"}
        if messages[-1]["role"] != "tool":
            message = {
                "tool_calls": [
                    {
                        "id": "write",
                        "function": {
                            "name": "file_write",
                            "arguments": json.dumps(
                                {
                                    "path": prompt["task"]["outputs"][0],
                                    "text": match[0] if match else "无法恢复约定。",
                                }
                            ),
                        },
                    }
                ]
            }
        return httpx.Response(200, json={"choices": [{"message": message}]})

    client = DeepSeekClient(
        base_url="https://api.deepseek.com",
        api_key="fixture",
        model="fixture",
        transport=httpx.MockTransport(handler),
    )
    try:
        evidence_path, passed = await run_pair(
            tmp_path, client, mode="mock-http", recall_enabled=enabled
        )
    finally:
        await client.aclose()
    evidence = json.loads(evidence_path.read_text())
    assert passed and all(evidence["checks"].values())
    assert len(payloads) == 4
    assert len({item["run_id"] for item in evidence["configs"]}) == 2
    trace = (evidence_path.parent / evidence["trace"]).read_text()
    assert "MEM-" not in trace and "Relevant private memory" not in trace
    rows = [json.loads(line) for line in trace.splitlines()]
    events = [row for row in rows if row["event"] == "memory_lifecycle"]
    assert all("memory_ids" not in row for row in events if row["phase"] != "retrieved")
    recalled = [row for row in events if row["task_id"] == "recall" and row["phase"] == "retrieved"]
    assert len(recalled) == int(enabled)
    if enabled:
        assert len(recalled[0]["memory_ids"]) == 1


async def test_failed_worker_model_call_marks_injection_failure_and_reflects(tmp_path):
    events = []

    class Observer:
        async def memory_event(self, event):
            events.append(event)

    class FailingModel:
        async def complete(self, messages, tools, *, max_tokens):
            assert "Synthetic remembered lesson" in messages[-1].content
            raise ModelCallError("model unavailable")

    memory = FakeMemory()
    service = build_service(
        root=tmp_path,
        model=FailingModel(),
        memory=memory,
        memory_observer=Observer(),
        profiles={"planner": profile("planner", role="planner"), "writer": profile()},
        tool_definitions=[],
        executor=FixtureTools(tmp_path),
        verifier=StubVerifier(),
        approver=PolicyApprover(),
    )
    result = await service.run_graph(
        graph(task(max_retries=0)),
        config(memory_enabled=True, write_memory=True),
    )
    assert result.goal_verdict is None
    assert [e.phase for e in events] == ["retrieved", "injected", "reflected"]
    assert [e.ok for e in events] == [True, False, True]
    assert events[1].error_type == "ModelCallError"
    assert events[1].model_step == 1
    assert events[0].context_sha256 == events[1].context_sha256
    assert len(memory.writes) == 1


async def test_observer_failure_is_reported_without_overriding_verdict(tmp_path):
    class BrokenObserver:
        async def memory_event(self, event):
            raise OSError("private observer failure details")

    memory = FakeMemory()
    service = build_service(
        root=tmp_path,
        model=ScriptedModel(write_response(), "Done"),
        memory=memory,
        memory_observer=BrokenObserver(),
        profiles={
            "planner": profile("planner", role="planner"),
            "writer": profile(tools=["file_write"]),
        },
        tool_definitions=[write_tool()],
        executor=FixtureTools(tmp_path),
        verifier=StubVerifier(),
        approver=PolicyApprover(),
    )
    result = await service.run_graph(graph(task()), config(memory_enabled=True, write_memory=True))
    assert result.goal_verdict.passed and len(memory.writes) == 1
    assert result.tasks[0].warnings == [
        "Memory observer retrieved failed: OSError",
        "Memory observer injected failed: OSError",
        "Memory observer reflected failed: OSError",
    ]
