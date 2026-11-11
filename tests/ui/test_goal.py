"""Run CLI goal assembly through real A planning/runtime with injected I/O."""

import asyncio
import json

import pytest

from protocol.models import (
    AcceptanceRule,
    AgentProfile,
    FailureCode,
    ModelResponse,
    Task,
    TaskGraph,
    ToolCall,
    ToolDefinition,
    ToolResult,
)
from ui import goal as goal_module
from ui.goal import GoalReport, run_goal

REPORT_TEXT = "# 生成报告\n\nverified phrase\n"


@pytest.fixture
def profiles_dir(tmp_path):
    directory = tmp_path / "profiles"
    directory.mkdir()
    profiles = [
        AgentProfile(agent_id="planner", role="planner", system_prompt="Plan the work."),
        AgentProfile(
            agent_id="writer", role="worker", system_prompt="Write reports.", tools=["file_write"]
        ),
        AgentProfile(
            agent_id="coder", role="worker", system_prompt="Execute code.", tools=["code_run"]
        ),
    ]
    for profile in profiles:
        # JSON is valid YAML, keeping the fixture independent of YAML formatting.
        (directory / f"{profile.agent_id}.yaml").write_text(
            profile.model_dump_json(), encoding="utf-8"
        )
    return directory


@pytest.fixture
def tool_backend():
    calls = []

    def factory(root):
        class FileExecutor:
            async def execute(self, call, context):
                calls.append(call)
                assert call.name == "file_write" and call.arguments["path"] == "outputs/report.md"
                assert not context.config.memory_enabled and not context.config.write_memory
                output = root / "workspaces" / context.agent_id / call.arguments["path"]
                output.write_text(call.arguments["text"], encoding="utf-8")
                return ToolResult(ok=True, data={"path": call.arguments["path"]})

        return FileExecutor(), [
            ToolDefinition(
                name="file_write", description="Write a file", parameters={"type": "object"}
            )
        ]

    return factory, calls


class ScriptedClient:
    model = "deepseek-chat"

    def __init__(self, *, invalid_plan=False):
        self.closed = False
        self.invalid_plan = invalid_plan
        self.planning_requests = []
        self.calls = 0
        self.graph = TaskGraph(
            tasks=[
                Task(
                    task_id="write-report",
                    title="Write a report",
                    assignee="writer",
                    outputs=["outputs/report.md"],
                    acceptance=[AcceptanceRule(type="file_exists", path="outputs/report.md")],
                    max_retries=0,
                )
            ],
            final_task_id="write-report",
            goal_acceptance=[AcceptanceRule(type="file_exists", path="report.md")],
        )

    async def complete(self, messages, tools, *, max_tokens):
        self.calls += 1
        if self.invalid_plan:
            return ModelResponse(content="Not a valid plan", tokens=1)
        if not tools:
            payload = json.loads(messages[-1].content)
            self.planning_requests.append(payload)
            if "profile" in payload:
                return ModelResponse(
                    content=json.dumps({"task_ids": ["write-report"], "rationale": "I can write"}),
                    tokens=3,
                )
            return ModelResponse(content=self.graph.model_dump_json(), tokens=5)
        if messages[-1].role == "user":
            return ModelResponse(
                tool_calls=[
                    ToolCall(
                        call_id="write",
                        name="file_write",
                        arguments={"path": "outputs/report.md", "text": REPORT_TEXT},
                    )
                ],
                tokens=2,
            )
        return ModelResponse(content="Done", tokens=3)

    async def aclose(self):
        self.closed = True


@pytest.mark.parametrize(
    "required_text, passed", [("verified phrase", True), ("missing text", False)]
)
async def test_goal_runs_planning_and_enforces_supplied_acceptance(
    tmp_path, profiles_dir, tool_backend, required_text, passed, capsys
):
    factory, calls = tool_backend
    client = ScriptedClient()
    rules = [AcceptanceRule(type="text_contains", path="report.md", text=required_text)]
    path, report = await run_goal(
        "写一份报告",
        tmp_path / "runs",
        rules=rules,
        profiles_dir=profiles_dir,
        client_factory=lambda: client,
        tool_factory=factory,
    )
    assert client.closed
    assert report.status == ("success" if passed else "failed")
    assert report.subtasks_passed == report.subtasks_total == 1
    assert report.acceptance_evaluated and report.acceptance_passed == int(passed)
    assert report.raw_result.graph.goal_acceptance == rules
    assert report.tokens == 5 and report.token_scope == "worker_only"
    assert report.duration_scope == "goal_with_planning"
    assert len(calls) == 1
    initial = client.planning_requests[0]
    assert initial["goal"] == "写一份报告"
    assert initial["required_goal_acceptance"] == [rule.model_dump() for rule in rules]
    assert [worker["agent_id"] for worker in initial["workers"]] == ["writer"]
    assert any("specialist_bids" in payload for payload in client.planning_requests)
    assert path.name == "evaluation.json" and path.parent.name == report.run_id
    stored = GoalReport.model_validate_json(path.read_text(encoding="utf-8"))
    assert stored.tasks == [report] and stored.requested_model == "deepseek-chat"
    assert stored.mode == "live"
    assert stored.summary.goal_completion_rate == float(passed)
    assert stored.summary.subtask_completion_rate == 1.0
    assert len(report.artifacts) == 1
    assert (path.parent / report.artifacts[0]).read_text(encoding="utf-8") == REPORT_TEXT
    events = [
        json.loads(line) for line in (path.parent / report.trace_path).read_text().splitlines()
    ]
    assert events[0]["event"] == "plan_ready" and events[-1]["event"] == "run_finished"
    assert events[-1]["status"] == report.status
    assert "Available workers: writer" in capsys.readouterr().out


async def test_no_plan_preserves_failed_goal_and_undefined_node_rate(
    tmp_path, profiles_dir, tool_backend
):
    factory, calls = tool_backend
    client = ScriptedClient(invalid_plan=True)
    path, report = await run_goal(
        "Write report",
        tmp_path / "runs",
        profiles_dir=profiles_dir,
        client_factory=lambda: client,
        tool_factory=factory,
    )
    assert client.closed and client.calls == 2 and calls == []
    assert report.status == "failed" and report.raw_result.graph is None
    assert report.raw_result.failure_code == FailureCode.MODEL_ERROR
    assert report.subtasks_total == report.subtasks_passed == 0
    assert not report.acceptance_evaluated and report.acceptance_total == 0
    assert report.artifacts == []
    stored = GoalReport.model_validate_json(path.read_text(encoding="utf-8"))
    assert stored.summary.goal_completion_rate == 0
    assert stored.summary.subtask_completion_rate is None
    assert "subtask_completion_rate" in stored.summary.undefined_reasons
    trace = (path.parent / report.trace_path).read_text(encoding="utf-8")
    assert json.loads(trace)["event"] == "run_finished"


async def test_cancellation_closes_client(tmp_path, profiles_dir, tool_backend):
    factory, _ = tool_backend
    started = asyncio.Event()

    class WaitingClient(ScriptedClient):
        async def complete(self, *args, **kwargs):
            started.set()
            await asyncio.Event().wait()

    client = WaitingClient()
    running = asyncio.create_task(
        run_goal(
            "Write report",
            tmp_path / "runs",
            profiles_dir=profiles_dir,
            client_factory=lambda: client,
            tool_factory=factory,
        )
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
    finally:
        if not running.done():
            running.cancel()
        await asyncio.gather(running, return_exceptions=True)
    assert client.closed


async def test_bootstrap_error_still_closes_created_client(
    tmp_path, profiles_dir, tool_backend, monkeypatch
):
    factory, _ = tool_backend
    client = ScriptedClient()

    def fail_bootstrap(**kwargs):
        raise RuntimeError("Bootstrap failed")

    monkeypatch.setattr(goal_module, "build_service", fail_bootstrap)
    with pytest.raises(RuntimeError, match="Bootstrap failed"):
        await run_goal(
            "Write report",
            tmp_path / "runs",
            profiles_dir=profiles_dir,
            client_factory=lambda: client,
            tool_factory=factory,
        )
    assert client.closed
