import json

import httpx
import pytest

from orchestrator.approvals import PolicyApprover
from orchestrator.bootstrap import build_service
from orchestrator.planner import Planner, PlanningError
from orchestrator.scheduler import Scheduler
from orchestrator.service import CollaborationService
from protocol.models import (
    AcceptanceRule,
    ChatMessage,
    ExecutionContext,
    FailureCode,
    ModelResponse,
    TaskStatus,
    ToolCall,
    ToolDefinition,
)
from runtime.agent import AgentRuntime
from runtime.client import DeepSeekClient, ModelCallError
from runtime.profiles import load_profiles
from tests.helpers import (
    FakeMemory,
    FixtureTools,
    RecordingObserver,
    ScriptedModel,
    StubVerifier,
    config,
    graph,
    profile,
    task,
)


def write_tool(*, approval=False):
    return ToolDefinition(
        name="file_write",
        description="Write an explicit synthetic test fixture",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["path", "text"],
        },
        requires_approval=approval,
    )


def write_response(call_id="write1"):
    return ModelResponse(
        tokens=10,
        tool_calls=[
            ToolCall(
                call_id=call_id,
                name="file_write",
                arguments={"path": "outputs/t1.md", "text": "Synthetic end-to-end fixture"},
            )
        ],
    )


async def test_goal_to_artifact_with_memory_and_approval(tmp_path):
    planned = graph(task())
    model = ScriptedModel(
        planned.model_dump_json(),
        '{"task_ids":["t1"],"rationale":"I can write the fixture"}',
        planned.model_dump_json(),
        write_response(),
        ModelResponse(content="Fixture written", tokens=5),
    )
    memory = FakeMemory()
    observer = RecordingObserver()
    worker_profile = profile(tools=["file_write"])
    executor = FixtureTools(tmp_path)
    service = build_service(
        root=tmp_path,
        model=model,
        profiles={
            "planner": profile("planner", role="planner"),
            "writer": worker_profile,
        },
        tool_definitions=[write_tool(approval=True)],
        executor=executor,
        verifier=StubVerifier(),
        approver=PolicyApprover(frozenset({"tool:file_write"})),
        memory=memory,
        observer=observer,
    )
    result = await service.run_goal(
        "Write a synthetic fixture",
        config(
            run_id="integration",
            memory_enabled=True,
            write_memory=True,
        ),
    )
    assert result.goal_verdict.passed
    assert result.tasks[0].status == TaskStatus.SUCCESS
    assert result.tasks[0].tokens == 15
    assert result.tasks[0].attempts == 1
    assert len(result.tasks[0].hitl_decisions) == 1
    assert memory.reads == [("writer", "t1")] and len(memory.writes) == 1
    assert len(executor.calls) == 1
    assert (
        tmp_path / "artifacts/integration/t1/t1.md"
    ).read_text() == "Synthetic end-to-end fixture"
    assert service.scheduler.worker.sessions == {}
    assert observer.decisions and observer.plans


async def test_disabled_memory_never_reads_history(tmp_path):
    memory = FakeMemory()
    model = ScriptedModel("No tools needed")
    runtime = AgentRuntime(model, FixtureTools(tmp_path), {"writer": profile()}, [], memory=memory)
    outcome = await runtime.execute(
        task(),
        ExecutionContext(
            config=config(),
            task_id="t1",
            agent_id="writer",
            attempt=1,
        ),
    )
    assert outcome.kind == "finished" and memory.reads == []
    assert "Synthetic remembered lesson" not in model.calls[0][0][-1].content


async def test_step_budget_stops_repeated_tool_requests(tmp_path):
    model = ScriptedModel(write_response())
    runtime = AgentRuntime(
        model, FixtureTools(tmp_path), {"writer": profile(tools=["file_write"])}, [write_tool()]
    )
    outcome = await runtime.execute(
        task(),
        ExecutionContext(
            config=config(max_agent_steps=1),
            task_id="t1",
            agent_id="writer",
            attempt=1,
        ),
    )
    assert outcome.failure_code == FailureCode.BUDGET_EXCEEDED
    assert len(model.calls) == 1


async def test_runtime_delegate_resumes_with_structured_child_results(tmp_path):
    child = task("child", "specialist")
    model = ScriptedModel(
        ModelResponse(
            tool_calls=[
                ToolCall(
                    call_id="delegate1",
                    name="delegate",
                    arguments={"tasks": [child.model_dump()]},
                )
            ]
        ),
        "Use the child's synthetic output",
    )
    runtime = AgentRuntime(
        model,
        FixtureTools(tmp_path),
        {"writer": profile(), "specialist": profile("specialist")},
        [],
    )
    context = ExecutionContext(config=config(), task_id="t1", agent_id="writer", attempt=1)
    first = await runtime.execute(task(), context)
    assert first.kind == "delegate" and first.children == [child]
    second = await runtime.execute(task(), context)
    assert second.kind == "finished"
    assert model.calls[1][0][-1].role == "tool"
    assert model.calls[1][0][-1].tool_call_id == "delegate1"


async def test_planner_repairs_invalid_structured_output():
    planned = graph(task())
    model = ScriptedModel("not json", planned.model_dump_json())
    result = await Planner(model, profile("planner", role="planner")).plan(
        "Write fixture",
        [profile()],
        config(),
        negotiate=False,
    )
    assert result == planned
    assert "Repair this invalid plan" in model.calls[1][0][-1].content


async def test_harness_goal_criteria_are_preserved_through_negotiation():
    planned = graph(task())
    required = [AcceptanceRule(type="text_contains", path="t1.md", text="Required section")]
    model = ScriptedModel(
        planned.model_dump_json(),
        '{"task_ids":["t1"],"rationale":"I can write the fixture"}',
        planned.model_dump_json(),
    )
    result = await Planner(model, profile("planner", role="planner")).plan(
        "Write fixture",
        [profile()],
        config(),
        goal_acceptance=required,
    )
    assert result.goal_acceptance == required
    assert "Specialist bids" in result.summary


async def test_planning_call_budget_is_bounded():
    model = ScriptedModel("not json")
    with pytest.raises(PlanningError, match="budget"):
        await Planner(model, profile("planner", role="planner")).plan(
            "Write fixture",
            [profile()],
            config(max_planning_calls=1),
            negotiate=False,
        )
    assert len(model.calls) == 1


async def test_planning_failure_returns_a_record_for_the_harness(tmp_path):
    model = ScriptedModel("not json", "still not json")
    runtime = AgentRuntime(model, FixtureTools(tmp_path), {"writer": profile()}, [])
    service = CollaborationService(
        Planner(model, profile("planner", role="planner")),
        Scheduler(runtime, StubVerifier(), PolicyApprover(), root=tmp_path),
        [profile()],
    )
    result = await service.run_goal("Synthetic goal", config())
    assert result.graph is None and result.tasks == []
    assert result.failure_code == FailureCode.MODEL_ERROR
    assert result.finished_at >= result.started_at


async def test_multiple_tool_calls_survive_approval_pause(tmp_path):
    response = write_response()
    response.tool_calls.append(write_response("write2").tool_calls[0])
    model = ScriptedModel(response, "Both fixture writes completed")
    executor = FixtureTools(tmp_path)
    runtime = AgentRuntime(
        model, executor, {"writer": profile(tools=["file_write"])}, [write_tool(approval=True)]
    )
    result = await Scheduler(
        runtime,
        StubVerifier(),
        PolicyApprover(frozenset({"tool:file_write"})),
        root=tmp_path,
    ).run(graph(task()), config())
    assert result.tasks[0].status == TaskStatus.SUCCESS
    assert [call.call_id for call in executor.calls] == ["write1", "write2"]
    assert len(result.tasks[0].hitl_decisions) == 2
    assert [message.tool_call_id for message in model.calls[-1][0] if message.role == "tool"] == [
        "write1",
        "write2",
    ]


def test_profiles_load_only_declared_fields(tmp_path):
    path = tmp_path / "writer.yaml"
    path.write_text("agent_id: writer\nrole: worker\nsystem_prompt: Write fixture\ntools: []\n")
    assert load_profiles(tmp_path)["writer"].agent_id == "writer"
    path.write_text(path.read_text() + "undeclared_setting: true\n")
    with pytest.raises(ValueError, match="Extra inputs"):
        load_profiles(tmp_path)


async def test_deepseek_request_and_tool_response_use_wire_contract():
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "one",
                                    "function": {
                                        "name": "file_write",
                                        "arguments": '{"path":"outputs/t1.md","text":"fixture"}',
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {"total_tokens": 17},
            },
        )

    client = DeepSeekClient(
        base_url="https://api.deepseek.com/v1",
        api_key="synthetic-test-key",
        model="deepseek-chat",
        transport=httpx.MockTransport(handler),
    )
    try:
        result = await client.complete(
            [ChatMessage(role="user", content="fixture")], [write_tool()], max_tokens=100
        )
    finally:
        await client.aclose()
    assert result.tokens == 17 and result.tool_calls[0].arguments["text"] == "fixture"
    assert str(requests[0].url) == "https://api.deepseek.com/v1/chat/completions"
    assert json.loads(requests[0].content)["max_tokens"] == 100


async def test_transient_model_error_retries_but_auth_error_does_not(monkeypatch):
    requests = []

    async def no_delay(seconds):
        pass

    monkeypatch.setattr("runtime.client.asyncio.sleep", no_delay)

    async def handler(request):
        requests.append(request)
        return httpx.Response(503 if len(requests) == 1 else 401)

    client = DeepSeekClient(
        base_url="https://api.deepseek.com",
        api_key="synthetic-test-key",
        model="deepseek-chat",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(ModelCallError, match="HTTP 401"):
            await client.complete([ChatMessage(role="user", content="fixture")], [], max_tokens=20)
    finally:
        await client.aclose()
    assert len(requests) == 2
