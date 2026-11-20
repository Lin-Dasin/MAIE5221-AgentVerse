"""Execute one offline Git publication through the scheduler's approval boundary."""

import hashlib
import json
import os
from pathlib import Path

from bus.trace import TRACE_FILENAME, JsonlTrace, TracingToolExecutor
from harness.metrics import EvaluationReport, summarize_batch, summarize_result
from harness.reporting import write_report
from harness.verdict import FileVerifier
from orchestrator.approvals import InteractiveApprover, PolicyApprover
from orchestrator.bootstrap import build_service
from protocol.models import (
    AcceptanceRule,
    AgentProfile,
    ChatMessage,
    FailureCode,
    HITLDecision,
    HITLRequest,
    ModelResponse,
    RunConfig,
    Task,
    TaskGraph,
    ToolCall,
    ToolDefinition,
    ToolResult,
)
from sandbox.executor import SandboxToolExecutor
from tools.registry import build

OUTPUT = "outputs/report.md"
TOOLS = ("file_write", "git_commit", "git_push")


def fixture_text(approve: bool) -> str:
    return "T3 approved publication" if approve else "T3 denied publication"


class PublicationModel:
    """Issue the same ordered tool requests in both scenarios and retain their replies."""

    def __init__(self, text: str):
        self.calls = [
            ToolCall(call_id="write", name="file_write", arguments={"path": OUTPUT, "text": text}),
            ToolCall(call_id="commit", name="git_commit", arguments={"message": text}),
            ToolCall(call_id="push", name="git_push", arguments={}),
        ]
        self.results: dict[str, dict] = {}

    async def complete(
        self, messages: list[ChatMessage], tools: list[ToolDefinition], *, max_tokens: int
    ) -> ModelResponse:
        index = len(self.results)
        last = messages[-1]
        if last.role == "tool":
            expected = self.calls[index]
            result = ToolResult.model_validate_json(last.content)
            if last.tool_call_id != expected.call_id or not result.ok:
                raise ValueError(f"{expected.name} did not complete successfully")
            if not isinstance(result.data, dict):
                raise ValueError(f"{expected.name} returned an invalid payload")
            self.results[expected.name] = result.data
            index += 1
        elif last.role != "user" or index != 0:
            raise ValueError("Unexpected T3 tool sequence")
        if index == len(self.calls):
            return ModelResponse(content="Published the committed report.")
        return ModelResponse(tool_calls=[self.calls[index]])


async def run_case(root: Path, approve: bool) -> dict:
    """Run in a dedicated process configured with the local fixture remote."""
    root = root.resolve()
    remote = Path(os.environ.get("GITHUB_SANDBOX_REPO", ""))
    if remote != root.parent / "remote.git" or not remote.is_dir() or remote.resolve() != remote:
        raise ValueError("T3 requires the local remote.git repository created for this run")
    root.mkdir(parents=True, exist_ok=True)
    text = fixture_text(approve)
    task = Task(
        task_id="publish",
        title="Write, commit and publish the fixed report",
        assignee="writer",
        outputs=[OUTPUT],
        acceptance=[AcceptanceRule(type="text_contains", path=OUTPUT, text=text)],
        max_retries=0,
    )
    graph = TaskGraph(
        tasks=[task],
        final_task_id=task.task_id,
        goal_acceptance=[AcceptanceRule(type="text_contains", path="report.md", text=text)],
    )
    profiles = {
        "planner": AgentProfile(agent_id="planner", role="planner", system_prompt="Plan the goal."),
        "writer": AgentProfile(
            agent_id=task.assignee, role="worker", system_prompt=task.title, tools=list(TOOLS)
        ),
    }
    registry = build()
    executor = SandboxToolExecutor(root, {name: registry[name] for name in TOOLS})
    definitions = executor.tool_definitions()
    if {d.name for d in definitions if d.requires_approval} != {"git_push"}:
        raise ValueError("Expected git_push to be the approval-gated T3 tool")
    trace = JsonlTrace(root / TRACE_FILENAME)
    model = PublicationModel(text)
    policy = PolicyApprover(frozenset({"tool:git_push"}) if approve else frozenset())
    requests = []

    async def decide(request: HITLRequest) -> HITLDecision:
        requests.append((request.task_id, request.kind, request.action))
        return await policy.decide(request)

    config = RunConfig(memory_enabled=False, write_memory=False, max_agent_steps=4)
    service = build_service(
        root=root,
        model=model,
        profiles=profiles,
        tool_definitions=definitions,
        executor=TracingToolExecutor(executor, trace),
        verifier=FileVerifier(),
        observer=trace,
        approver=InteractiveApprover(decide),
    )
    raw = await service.run_graph(graph, config)
    await trace.run_finished(raw)
    scenario = "t3-approve" if approve else "t3-deny"
    case = summarize_result(scenario, len(graph.goal_acceptance), raw, config, root)
    case.artifacts = [Path(p).relative_to(root).as_posix() for p in case.artifacts]
    case.trace_path = TRACE_FILENAME if trace.path.is_file() else None
    case.warnings.extend(trace.warnings)
    evaluation = EvaluationReport(
        run_id=raw.run_id,
        suite=scenario,
        suite_sha256=hashlib.sha256(graph.model_dump_json().encode()).hexdigest(),
        started_at=raw.started_at,
        finished_at=raw.finished_at,
        tasks=[case],
        summary=summarize_batch([case]),
    )
    write_report(evaluation, root / "evaluation.json")

    events = [json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines()]
    operations = [e for e in events if e["event"] in {"hitl_decided", "tool_finished"}]
    expected_order = ["file_write", "git_commit", "hitl_decided"]
    if approve:
        expected_order.append("git_push")
    decisions = [e for e in operations if e["event"] == "hitl_decided"]
    commit = model.results.get("git_commit", {})
    push = model.results.get("git_push", {})
    checks = {
        "trace_complete": not case.warnings
        and bool(events)
        and events[-1]["event"] == "run_finished"
        and events[-1]["status"] == case.status
        and all(e["run_id"] == raw.run_id and e["sequence"] == n for n, e in enumerate(events, 1)),
        "tool_and_decision_order": [e.get("tool", e["event"]) for e in operations] == expected_order
        and all(e["ok"] for e in operations if e["event"] == "tool_finished"),
        "push_permission": requests == [(task.task_id, "permission", "tool:git_push")]
        and len(decisions) == 1
        and decisions[0]["approved"] is approve
        and decisions[0]["source"] == "policy",
        "local_commit": commit.get("committed") is True and commit.get("files") == [OUTPUT],
        "raw_goal_and_node": case.status == ("success" if approve else "failed")
        and case.subtasks_total == 1
        and case.subtasks_passed == int(approve),
    }
    if approve:
        checks["push_result"] = push.get("pushed") is True and push.get("remote") == str(remote)
    else:
        checks["blocked_before_push"] = (
            not push
            and len(raw.tasks) == 1
            and (raw.tasks[0].failure_code == FailureCode.HITL_BLOCKED)
        )
    return {
        "checks": checks,
        "branch": commit.get("branch"),
        "commit": commit.get("commit"),
        "push_branch": push.get("branch"),
        "raw_goal": case.status,
        "nodes_passed": case.subtasks_passed,
        "nodes_total": case.subtasks_total,
    }
