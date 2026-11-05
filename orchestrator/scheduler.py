"""Serial DAG execution with bounded retries and resumable delegation/HITL."""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from orchestrator.artifacts import ArtifactStore
from protocol.models import (
    AgentOutcome,
    ExecutionContext,
    FailureCode,
    HITLDecision,
    RunConfig,
    RunResult,
    Task,
    TaskGraph,
    TaskResult,
    TaskStatus,
)
from protocol.ports import Approver, Memory, Observer, Verifier, Worker

TERMINAL = {TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.BLOCKED}


@dataclass
class _Record:
    task: Task
    result: TaskResult
    children: list[str] = field(default_factory=list)
    feedback: list[str] = field(default_factory=list)
    decision: HITLDecision | None = None
    deadline: float = 0
    attempt_tokens: int = 0


class Scheduler:
    def __init__(
        self,
        worker: Worker,
        verifier: Verifier,
        approver: Approver,
        *,
        root: Path,
        memory: Memory | None = None,
        observer: Observer | None = None,
    ):
        self.worker = worker
        self.verifier = verifier
        self.approver = approver
        self.root = root
        self.memory = memory
        self.observer = observer
        self._running = False

    async def run(self, graph: TaskGraph, config: RunConfig) -> RunResult:
        if self._running:
            raise RuntimeError("one scheduler owns one serial workspace run at a time")
        if len(graph.tasks) > config.max_tasks or any(task.depth for task in graph.tasks):
            raise ValueError("initial graph must fit the task budget and contain depth-0 tasks")
        if config.write_memory and self.memory is None:
            raise ValueError("write_memory requires a Role C adapter")
        self._running = True
        self.config = config
        self.graph = graph.model_copy(deep=True)
        self.records = {task.task_id: self._new_record(task) for task in self.graph.tasks}
        self.warnings: list[str] = []
        started_at = datetime.now(UTC)
        lease = self.root / ".agentverse-state" / ".workspace-lease"
        lease_acquired = False
        try:
            lease.parent.mkdir(parents=True, exist_ok=True)
            try:
                lease.mkdir()
            except FileExistsError as exc:
                raise RuntimeError("this workspace root is already leased by another run") from exc
            lease_acquired = True
            self.store = ArtifactStore(self.root, config.run_id)
            await self._observe("plan_ready", config.run_id, self.graph.model_copy(deep=True))
            while any(record.result.status not in TERMINAL for record in self.records.values()):
                progressed = False
                for record in list(self.records.values()):
                    status = record.result.status
                    if status in TERMINAL:
                        continue
                    dependencies = [self.records[key] for key in record.task.depends_on]
                    if any(
                        item.result.status in {TaskStatus.FAILED, TaskStatus.BLOCKED}
                        for item in dependencies
                    ):
                        await self._finish(
                            record,
                            TaskStatus.BLOCKED,
                            FailureCode.DEPENDENCY_FAILED,
                            "A required dependency failed",
                        )
                    elif any(item.result.status != TaskStatus.SUCCESS for item in dependencies):
                        continue
                    elif status == TaskStatus.WAITING_CHILDREN:
                        children = [self.records[key] for key in record.children]
                        if any(item.result.status not in TERMINAL for item in children):
                            continue
                        if any(item.result.status != TaskStatus.SUCCESS for item in children):
                            await self._finish(
                                record,
                                TaskStatus.FAILED,
                                FailureCode.DEPENDENCY_FAILED,
                                "A delegated subtask failed",
                            )
                        else:
                            await self._step(record, resume=True)
                    else:
                        await self._step(record)
                    progressed = True
                    break
                if not progressed:
                    for record in self.records.values():
                        if record.result.status not in TERMINAL:
                            await self._finish(
                                record,
                                TaskStatus.BLOCKED,
                                FailureCode.DEPENDENCY_FAILED,
                                "No runnable task remains",
                            )
            verdict = None
            failure_reason = None
            failure_code = None
            if self.graph.final_task_id in self.store.accepted:
                try:
                    async with asyncio.timeout(config.task_timeout_sec):
                        verdict = await self.verifier.check(
                            self.graph.goal_acceptance,
                            self.store.archive / self.graph.final_task_id,
                        )
                    if not verdict.passed:
                        failure_reason = "Final artifact did not pass goal acceptance"
                        failure_code = FailureCode.ACCEPTANCE_FAILED
                except Exception as exc:
                    failure_reason = f"Goal verifier failed: {type(exc).__name__}"
                    failure_code = (
                        FailureCode.TIMEOUT
                        if isinstance(exc, TimeoutError)
                        else FailureCode.INTERNAL_ERROR
                    )
            else:
                failure_reason = "Final task did not publish accepted artifacts"
                failure_code = (
                    self.records[self.graph.final_task_id].result.failure_code
                    or FailureCode.MISSING_INPUT
                )
            return RunResult(
                run_id=config.run_id,
                graph=self.graph,
                tasks=[record.result for record in self.records.values()],
                started_at=started_at,
                finished_at=datetime.now(UTC),
                goal_verdict=verdict,
                failure_code=failure_code,
                failure_reason=failure_reason,
                warnings=self.warnings,
            )
        finally:
            try:
                if lease_acquired:
                    for task_id in self.records:
                        self.worker.release(config.run_id, task_id)
            finally:
                if lease_acquired:
                    lease.rmdir()
                self._running = False

    @staticmethod
    def _new_record(task: Task) -> _Record:
        return _Record(task, TaskResult(task_id=task.task_id, agent_id=task.assignee))

    async def _observe(self, method: str, *args: object) -> None:
        if self.observer is None:
            return
        try:
            async with asyncio.timeout(min(5, self.config.task_timeout_sec)):
                await getattr(self.observer, method)(*args)
        except Exception as exc:
            self.warnings.append(f"Observer {method} failed: {type(exc).__name__}")

    async def _state(self, record: _Record, status: TaskStatus) -> None:
        record.result.status = status
        await self._observe(
            "task_changed", self.config.run_id, record.task, record.result.model_copy(deep=True)
        )

    async def _finish(
        self,
        record: _Record,
        status: TaskStatus,
        code: FailureCode | None = None,
        reason: str | None = None,
    ) -> None:
        record.result.failure_code = code
        record.result.failure_reason = reason
        record.result.finished_at = datetime.now(UTC)
        await self._state(record, status)
        if self.config.write_memory and self.memory is not None:
            try:
                async with asyncio.timeout(min(5, self.config.task_timeout_sec)):
                    await self.memory.reflect(record.task, record.result.model_copy(deep=True))
            except Exception as exc:
                record.result.warnings.append(f"Reflection failed: {type(exc).__name__}")
        self.worker.release(self.config.run_id, record.task.task_id)
        self.store.discard_snapshot(record.task.task_id)

    async def _fail(
        self, record: _Record, code: FailureCode, reason: str, *, retryable: bool = True
    ) -> None:
        record.feedback = record.result.critique.defects if record.result.critique else [reason]
        self.worker.release(self.config.run_id, record.task.task_id)
        if retryable and record.result.attempts <= record.task.max_retries:
            record.result.failure_code = code
            record.result.failure_reason = reason
            record.decision = None
            await self._state(record, TaskStatus.PENDING)
        else:
            await self._finish(record, TaskStatus.FAILED, code, reason)

    async def _step(self, record: _Record, *, resume: bool = False) -> None:
        try:
            if record.result.status == TaskStatus.PENDING:
                record.result.attempts += 1
                record.attempt_tokens = 0
                record.deadline = time.monotonic() + self.config.task_timeout_sec
                record.result.started_at = record.result.started_at or datetime.now(UTC)
                record.result.critique = None
                self.store.prepare(record.task)
                await self._observe(
                    "artifacts_handed_off",
                    self.config.run_id,
                    record.task.task_id,
                    record.task.inputs,
                )
            elif resume:
                self.store.resume(record.task)
            children = [self.records[key] for key in record.children]
            inputs = self.store.child_inputs([child.task for child in children])
            if inputs:
                self.store.handoff(record.task, inputs)
                await self._observe(
                    "artifacts_handed_off", self.config.run_id, record.task.task_id, inputs
                )
            token_budget = self.config.max_task_tokens - (
                record.result.tokens - record.attempt_tokens
            )
            if token_budget <= 0:
                await self._fail(
                    record,
                    FailureCode.BUDGET_EXCEEDED,
                    "Task token budget was exhausted by previous attempts",
                    retryable=False,
                )
                return
            attempt_config = self.config.model_copy(update={"max_task_tokens": token_budget})
            context = ExecutionContext(
                config=attempt_config,
                task_id=record.task.task_id,
                agent_id=record.task.assignee,
                attempt=record.result.attempts,
                feedback=record.feedback,
                child_results=[child.result for child in children],
                child_inputs=inputs,
                decision=record.decision,
            )
            await self._state(record, TaskStatus.RUNNING)
            remaining = record.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            async with asyncio.timeout(remaining):
                outcome = await self.worker.execute(record.task, context)
            record.result.tokens += max(0, outcome.tokens - record.attempt_tokens)
            record.attempt_tokens = max(record.attempt_tokens, outcome.tokens)
            record.result.summary = outcome.summary
            record.decision = None
            if outcome.kind == "failed":
                await self._fail(
                    record,
                    outcome.failure_code or FailureCode.INTERNAL_ERROR,
                    outcome.summary,
                    retryable=outcome.retryable,
                )
            elif outcome.kind == "delegate":
                self._delegate(record, outcome)
                await self._observe(
                    "tasks_delegated",
                    self.config.run_id,
                    record.task,
                    [self.records[child.task_id].task for child in outcome.children],
                )
                await self._state(record, TaskStatus.WAITING_CHILDREN)
            elif outcome.kind == "ask_human":
                await self._approve(record, outcome)
            else:
                await self._state(record, TaskStatus.REVIEWING)
                remaining = record.deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                async with asyncio.timeout(remaining):
                    critique = await self.verifier.check(
                        record.task.acceptance, self.store.workspace(record.task.assignee)
                    )
                record.result.critique = critique
                if critique.passed:
                    self.store.publish(record.task)
                    record.result.outputs = record.task.outputs
                    await self._finish(record, TaskStatus.SUCCESS)
                else:
                    await self._fail(record, FailureCode.ACCEPTANCE_FAILED, "Acceptance failed")
        except TimeoutError:
            await self._fail(record, FailureCode.TIMEOUT, "Task execution timed out")
        except FileNotFoundError:
            await self._fail(record, FailureCode.MISSING_INPUT, "A declared file is missing")
        except ValueError as exc:
            await self._fail(record, FailureCode.INVALID_OUTPUT, str(exc), retryable=False)
        except Exception as exc:
            await self._fail(record, FailureCode.INTERNAL_ERROR, type(exc).__name__)

    def _delegate(self, record: _Record, outcome: AgentOutcome) -> None:
        if record.task.depth != 0 or len(record.children) + len(outcome.children) > 2:
            raise ValueError("delegation exceeds the depth or child-count limit")
        if len(self.records) + len(outcome.children) > self.config.max_tasks:
            raise ValueError("delegation exceeds the run task budget")
        new_ids = {child.task_id for child in outcome.children}
        if len(new_ids) != len(outcome.children) or new_ids.intersection(self.records):
            raise ValueError("delegated task IDs must be unique within the run")
        completed = {
            key for key, item in self.records.items() if item.result.status == TaskStatus.SUCCESS
        }
        children = []
        for child in outcome.children:
            if any(dep not in completed | new_ids for dep in child.depends_on):
                raise ValueError("subtasks may depend only on accepted tasks or sibling subtasks")
            data = child.model_dump()
            data.update(parent_task=record.task.task_id, depth=1)
            children.append(Task.model_validate(data))
        graph = TaskGraph(
            tasks=self.graph.tasks + children,
            final_task_id=self.graph.final_task_id,
            goal_acceptance=self.graph.goal_acceptance,
            summary=self.graph.summary,
        )
        self.store.suspend(record.task)
        self.graph = graph
        for child in children:
            self.records[child.task_id] = self._new_record(child)
            record.children.append(child.task_id)

    async def _approve(self, record: _Record, outcome: AgentOutcome) -> None:
        request = outcome.request
        if request is None or request.task_id != record.task.task_id:
            raise ValueError("HITL request must belong to the current task")
        await self._state(record, TaskStatus.WAITING_HUMAN)
        remaining = min(self.config.approval_timeout_sec, record.deadline - time.monotonic())
        try:
            if remaining <= 0:
                raise TimeoutError
            async with asyncio.timeout(remaining):
                decision = await self.approver.decide(request)
        except TimeoutError:
            decision = HITLDecision(
                request_id=request.request_id,
                approved=False,
                source="timeout",
                answer="Approval deadline expired",
            )
        if decision.request_id != request.request_id:
            raise ValueError("HITL response does not match the pending request")
        record.result.hitl_decisions.append(decision)
        record.decision = decision
        await self._observe("hitl_decided", self.config.run_id, request, decision)
        if not decision.approved:
            await self._finish(record, TaskStatus.FAILED, FailureCode.HITL_BLOCKED, decision.answer)
