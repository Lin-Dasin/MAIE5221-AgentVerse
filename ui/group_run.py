"""Run a goal for a group and publish its handoffs and result to the conversation."""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

from bus.messages import MessageBus, MessageBusError
from harness.metrics import CaseReport
from protocol.models import AcceptanceRule, InputRef, Task, TaskGraph
from ui.goal import ConsoleTrace, run_goal


def _event(event: str, run_id: str, **fields: object) -> str:
    return json.dumps({"event": event, "run_id": run_id, **fields}, ensure_ascii=False)


class GroupTrace(ConsoleTrace):
    def __init__(self, path: Path, bus: MessageBus, conversation: str, notifier: str):
        super().__init__(path)
        self.bus = bus
        self.conversation = conversation
        self.notifier = notifier
        self.assignee_by_task: dict[str, str] = {}

    async def plan_ready(self, run_id: str, graph: TaskGraph) -> None:
        await super().plan_ready(run_id, graph)
        self.assignee_by_task.update({task.task_id: task.assignee for task in graph.tasks})

    async def tasks_delegated(self, run_id: str, parent: Task, children: list[Task]) -> None:
        await super().tasks_delegated(run_id, parent, children)
        self.assignee_by_task.update({task.task_id: task.assignee for task in children})

    async def artifacts_handed_off(self, run_id: str, task_id: str, inputs: list[InputRef]) -> None:
        await super().artifacts_handed_off(run_id, task_id, inputs)
        if not inputs:
            return
        event = _event(
            "artifacts_handed_off",
            run_id,
            task_id=task_id,
            agent_id=self.assignee_by_task.get(task_id),
            inputs=[
                {"from_task": item.from_task, "path": item.path, "destination": item.destination}
                for item in inputs
            ],
        )
        try:
            self.bus.publish(self.conversation, self.notifier, "handoff", event)
        except (MessageBusError, OSError) as error:
            self.warnings.append(f"Group handoff notification failed: {type(error).__name__}")


async def run_group_goal(
    bus: MessageBus,
    conversation: str,
    sender: str,
    notifier: str,
    goal: str,
    work_dir: Path,
    *,
    rules: list[AcceptanceRule] | None = None,
    profiles_dir: Path | None = None,
) -> tuple[Path, CaseReport]:
    sender, notifier = sender.strip(), notifier.strip()
    goal = goal.strip()
    if not goal:
        raise ValueError("Goal must contain text")
    worker_ids = bus.group_members(conversation, sender, notifier) - {sender, notifier}
    if not worker_ids:
        raise ValueError("Add a Worker to the group before submitting a task")

    run_id = uuid4().hex
    bus.publish(conversation, sender, "message", _event("run_requested", run_id, goal=goal))

    def trace_factory(path: Path) -> GroupTrace:
        return GroupTrace(path, bus, conversation, notifier)

    try:
        path, report = await run_goal(
            goal,
            work_dir,
            rules=rules,
            profiles_dir=profiles_dir,
            run_id=run_id,
            worker_ids=worker_ids,
            trace_factory=trace_factory,
        )
    except asyncio.CancelledError:
        bus.publish(conversation, notifier, "message", _event("run_cancelled", run_id))
        raise
    except (OSError, RuntimeError, ValueError):
        bus.publish(conversation, notifier, "message", _event("run_failed", run_id))
        raise

    bus.publish(
        conversation,
        notifier,
        "message",
        _event(
            "run_finished",
            run_id,
            status=report.status,
            evaluation=str(path),
            artifacts=[str(path.parent / artifact) for artifact in report.artifacts],
            trace=str(path.parent / report.trace_path) if report.trace_path else None,
            subtasks_passed=report.subtasks_passed,
            subtasks_total=report.subtasks_total,
            failure_reason=report.failure_reason,
            warnings=report.warnings,
        ),
    )
    return path, report
