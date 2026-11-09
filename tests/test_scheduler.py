import asyncio

import pytest

from orchestrator.approvals import InteractiveApprover, PolicyApprover
from orchestrator.scheduler import Scheduler
from protocol.models import AgentOutcome, FailureCode, HITLRequest, InputRef, TaskStatus
from tests.helpers import (
    FakeMemory,
    RecordingObserver,
    ScriptedWorker,
    StubVerifier,
    config,
    failed,
    graph,
    passed,
    task,
)


def scheduler(root, worker, verifier=None, **kwargs):
    return Scheduler(worker, verifier or StubVerifier(), PolicyApprover(), root=root, **kwargs)


async def test_dependencies_handoffs_and_memory_lifetime(tmp_path):
    memory_file = tmp_path / "workspaces/writer/memory/writer.jsonl"
    memory_file.parent.mkdir(parents=True)
    memory_file.write_text("synthetic persisted memory\n")
    first = task("first")
    last = task(
        "last",
        depends_on=["first"],
        inputs=[
            InputRef(
                from_task="first",
                path="first.md",
                destination="inputs/source.md",
            )
        ],
    )
    worker = ScriptedWorker(tmp_path)
    observer = RecordingObserver()
    verifier = StubVerifier()
    result = await scheduler(tmp_path, worker, verifier, observer=observer).run(
        graph(last, first, final="last"), config(run_id="handoff")
    )
    assert [item.task_id for item, _ in worker.calls] == ["first", "last"]
    assert all(item.status == TaskStatus.SUCCESS for item in result.tasks)
    assert (
        tmp_path / "workspaces/writer/inputs/source.md"
    ).read_text() == "Synthetic output: first"
    assert (tmp_path / "artifacts/handoff/last/last.md").exists()
    assert memory_file.read_text() == "synthetic persisted memory\n"
    assert verifier.calls[-1][1] == tmp_path / "artifacts/handoff/last"
    assert observer.plans and observer.handoffs


async def test_failed_acceptance_retries_without_duplicate_nodes(tmp_path):
    worker = ScriptedWorker(tmp_path)
    memory = FakeMemory()
    result = await scheduler(tmp_path, worker, StubVerifier(failed(), passed()), memory=memory).run(
        graph(task(max_retries=1)), config(write_memory=True)
    )
    assert len(result.tasks) == 1
    assert result.tasks[0].attempts == 2
    assert result.tasks[0].status == TaskStatus.SUCCESS
    assert worker.calls[1][1].feedback == ["Missing section"]
    assert len(memory.writes) == 1


async def test_task_token_budget_carries_over_retries(tmp_path):
    worker = ScriptedWorker(
        tmp_path,
        {
            "t1": [
                AgentOutcome(
                    kind="failed",
                    tokens=7,
                    failure_code=FailureCode.TOOL_ERROR,
                    summary="Synthetic transient tool failure",
                )
            ]
        },
    )
    result = await scheduler(tmp_path, worker).run(
        graph(task(max_retries=1)), config(max_task_tokens=10)
    )
    assert result.tasks[0].status == TaskStatus.SUCCESS
    assert worker.calls[1][1].config.max_task_tokens == 3
    assert result.tasks[0].tokens == 7


async def test_exhausted_retry_blocks_downstream_and_does_not_publish(tmp_path):
    worker = ScriptedWorker(tmp_path)
    result = await scheduler(tmp_path, worker, StubVerifier(failed(), failed())).run(
        graph(task("first", max_retries=1), task("last", depends_on=["first"])),
        config(run_id="failed"),
    )
    assert [item.status for item in result.tasks] == [TaskStatus.FAILED, TaskStatus.BLOCKED]
    assert len(worker.calls) == 2
    assert not (tmp_path / "artifacts/failed/first").exists()
    assert result.goal_verdict is None


async def test_optional_failure_does_not_override_goal_verdict(tmp_path):
    worker = ScriptedWorker(tmp_path)
    result = await scheduler(tmp_path, worker, StubVerifier(failed(), passed(), passed())).run(
        graph(task("optional", max_retries=0), task("final")), config()
    )
    assert result.tasks[0].status == TaskStatus.FAILED
    assert result.goal_verdict.passed


async def test_parent_workspace_survives_same_agent_delegation(tmp_path):
    child = task("child")

    async def delegate(parent, context):
        (tmp_path / "workspaces/writer/outputs/draft.md").write_text("parent draft")
        return AgentOutcome(kind="delegate", children=[child])

    async def resume(parent, context):
        assert (tmp_path / "workspaces/writer/outputs/draft.md").read_text() == "parent draft"
        assert (tmp_path / "workspaces/writer/inputs/delegated/child/child.md").exists()
        assert context.child_results[0].status == TaskStatus.SUCCESS
        (tmp_path / "workspaces/writer/outputs/parent.md").write_text("combined synthetic result")
        return AgentOutcome(kind="finished")

    worker = ScriptedWorker(tmp_path, {"parent": [delegate, resume]})
    result = await scheduler(tmp_path, worker).run(graph(task("parent")), config())
    assert [item.task_id for item, _ in worker.calls] == ["parent", "child", "parent"]
    assert result.tasks[0].attempts == 1
    assert result.graph.tasks[-1].parent_task == "parent"
    assert all(item.status == TaskStatus.SUCCESS for item in result.tasks)


async def test_nested_delegation_ends_cleanly(tmp_path):
    worker = ScriptedWorker(
        tmp_path,
        {
            "parent": [AgentOutcome(kind="delegate", children=[task("child")])],
            "child": [AgentOutcome(kind="delegate", children=[task("grandchild")])],
        },
    )
    result = await scheduler(tmp_path, worker).run(graph(task("parent")), config())
    assert len(result.tasks) == 2
    assert result.tasks[1].failure_code == FailureCode.INVALID_OUTPUT
    assert result.tasks[0].failure_code == FailureCode.DEPENDENCY_FAILED


async def test_policy_approval_is_recorded_and_resumed(tmp_path):
    request = HITLRequest(
        task_id="t1", kind="clarification", action="language", question="Which language?"
    )
    worker = ScriptedWorker(tmp_path, {"t1": [AgentOutcome(kind="ask_human", request=request)]})
    observer = RecordingObserver()
    engine = Scheduler(
        worker,
        StubVerifier(),
        PolicyApprover(clarification_answers={"language": "English"}),
        root=tmp_path,
        observer=observer,
    )
    result = await engine.run(graph(task()), config())
    decision = result.tasks[0].hitl_decisions[0]
    assert decision.answer == "English" and decision.assumptions
    assert worker.calls[1][1].decision == decision
    assert observer.decisions == [decision]
    assert result.tasks[0].attempts == 1


async def test_unattended_permission_denial_is_terminal(tmp_path):
    request = HITLRequest(task_id="t1", kind="permission", action="publish", question="Publish?")
    worker = ScriptedWorker(tmp_path, {"t1": [AgentOutcome(kind="ask_human", request=request)]})
    result = await scheduler(tmp_path, worker).run(graph(task()), config())
    assert result.tasks[0].failure_code == FailureCode.HITL_BLOCKED
    assert result.tasks[0].attempts == 1
    assert len(worker.calls) == 1


async def test_interactive_timeout_returns_a_decision():
    async def unanswered(request):
        await asyncio.Event().wait()

    request = HITLRequest(task_id="t1", kind="clarification", action="language", question="Which?")
    result = await InteractiveApprover(unanswered, timeout_sec=0.01).decide(request)
    assert not result.approved and result.source == "timeout"


async def test_task_timeout_is_bounded(tmp_path):
    async def wait_forever(task, context):
        await asyncio.Event().wait()

    worker = ScriptedWorker(tmp_path, {"t1": [wait_forever]})
    result = await scheduler(tmp_path, worker).run(
        graph(task(max_retries=0)), config(task_timeout_sec=0.02)
    )
    assert result.tasks[0].failure_code == FailureCode.TIMEOUT


async def test_observer_failure_keeps_execution_and_reports_warning(tmp_path):
    class FailingObserver(RecordingObserver):
        async def task_changed(self, *args):
            raise OSError("synthetic unavailable observer")

    result = await scheduler(tmp_path, ScriptedWorker(tmp_path), observer=FailingObserver()).run(
        graph(task()), config()
    )
    assert result.goal_verdict.passed and result.warnings


async def test_run_id_reuse_is_rejected(tmp_path):
    engine = scheduler(tmp_path, ScriptedWorker(tmp_path))
    await engine.run(graph(task()), config(run_id="once"))
    with pytest.raises(ValueError, match="already has artifacts"):
        await engine.run(graph(task()), config(run_id="once"))


async def test_two_child_limit_applies_across_multiple_requests(tmp_path):
    worker = ScriptedWorker(
        tmp_path,
        {
            "parent": [
                AgentOutcome(kind="delegate", children=[task("child1"), task("child2")]),
                AgentOutcome(kind="delegate", children=[task("child3")]),
            ]
        },
    )
    observer = RecordingObserver()
    result = await scheduler(tmp_path, worker, observer=observer).run(
        graph(task("parent")), config()
    )
    assert len(result.tasks) == 3
    assert result.tasks[0].failure_code == FailureCode.INVALID_OUTPUT
    assert len(observer.delegations) == 1


async def test_separate_schedulers_cannot_share_an_active_workspace(tmp_path):
    started = asyncio.Event()
    resume = asyncio.Event()

    async def hold(task, context):
        started.set()
        await resume.wait()
        (tmp_path / "workspaces/writer/outputs/t1.md").write_text("synthetic fixture")
        return AgentOutcome(kind="finished")

    first = scheduler(tmp_path, ScriptedWorker(tmp_path, {"t1": [hold]}))
    second = scheduler(tmp_path, ScriptedWorker(tmp_path))
    running = asyncio.create_task(first.run(graph(task()), config()))
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        with pytest.raises(RuntimeError, match="already leased"):
            await second.run(graph(task()), config())
    finally:
        resume.set()
        await running
    assert not (tmp_path / ".agentverse-state/.workspace-lease").exists()
