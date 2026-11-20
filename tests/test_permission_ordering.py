"""A's permission boundary: no queued side effect before its own approval."""

import asyncio

import pytest

from harness.verdict import FileVerifier
from orchestrator.bootstrap import build_service
from protocol.models import (
    FailureCode,
    HITLDecision,
    ModelResponse,
    ToolCall,
    ToolDefinition,
    ToolResult,
)
from tests.helpers import RecordingObserver, ScriptedModel, config, graph, profile, task
from tests.test_runtime import write_response, write_tool


class ApprovalTrace(RecordingObserver):
    def __init__(self, order):
        super().__init__()
        self.order = order

    async def hitl_decided(self, run_id, request, decision):
        await super().hitl_decided(run_id, request, decision)
        self.order.append(("decision", decision.approved))


class RecordingExecutor:
    def __init__(self, root, order):
        self.root, self.order = root, order

    async def execute(self, call, context):
        self.order.append(("execute", call.call_id))
        if call.name == "file_write":
            path = self.root / "workspaces" / context.agent_id / call.arguments["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(call.arguments["text"])
        return ToolResult(ok=True, data={"fixture": True})


def app(root, model, approver, order):
    return build_service(
        root=root,
        model=model,
        profiles={
            "planner": profile("planner", role="planner"),
            "writer": profile(tools=["file_write", "git_push"]),
        },
        tool_definitions=[
            write_tool(),
            ToolDefinition(
                name="git_push",
                description="Synthetic protected operation",
                parameters={},
                requires_approval=True,
            ),
        ],
        executor=RecordingExecutor(root, order),
        verifier=FileVerifier(),
        approver=approver,
        observer=ApprovalTrace(order),
    )


def queued_pushes():
    return ModelResponse(
        tool_calls=[
            *write_response().tool_calls,
            ToolCall(call_id="push1", name="git_push"),
            ToolCall(call_id="push2", name="git_push"),
            *write_response("after-push").tool_calls,
        ]
    )


@pytest.mark.parametrize("decision", ["deny", "timeout", "mismatch"])
async def test_denied_expired_or_wrong_decision_discards_queue_without_retry(tmp_path, decision):
    order = []

    class Approver:
        async def decide(self, request):
            if decision == "timeout":
                await asyncio.Event().wait()
            return HITLDecision(
                request_id="wrong-request" if decision == "mismatch" else request.request_id,
                approved=decision == "mismatch",
                source="human",
            )

    model = ScriptedModel(queued_pushes())
    service = app(tmp_path, model, Approver(), order)
    result = await service.run_graph(
        graph(task(max_retries=3)),
        config(approval_timeout_sec=0.01),
    )
    node = result.tasks[0]
    assert node.attempts == 1
    assert node.failure_code == (
        FailureCode.INVALID_OUTPUT if decision == "mismatch" else FailureCode.HITL_BLOCKED
    )
    assert [entry for entry in order if entry[0] == "execute"] == [("execute", "write1")]
    assert len(model.calls) == 1 and service.scheduler.worker.sessions == {}
    assert result.goal_verdict is None
    if decision != "mismatch":
        assert len(node.hitl_decisions) == 1 and not node.hitl_decisions[0].approved


async def test_each_queued_push_requires_its_own_decision(tmp_path):
    order, request_ids = [], []

    class Approver:
        async def decide(self, request):
            request_ids.append(request.request_id)
            return HITLDecision(
                request_id=request.request_id,
                approved=len(request_ids) == 1,
                source="human",
            )

    service = app(tmp_path, ScriptedModel(queued_pushes()), Approver(), order)
    result = await service.run_graph(graph(task(max_retries=3)), config())
    assert order == [
        ("execute", "write1"),
        ("decision", True),
        ("execute", "push1"),
        ("decision", False),
    ]
    assert len(set(request_ids)) == 2
    assert result.tasks[0].attempts == 1
    assert result.tasks[0].failure_code == FailureCode.HITL_BLOCKED
    assert service.scheduler.worker.sessions == {}
