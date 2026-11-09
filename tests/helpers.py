"""Isolated test doubles for the not-yet-implemented B/C/D adapters."""

from collections import deque
from pathlib import Path

from protocol.models import (
    AcceptanceRule,
    AgentOutcome,
    AgentProfile,
    CriticResult,
    ModelResponse,
    RunConfig,
    Task,
    TaskGraph,
    ToolResult,
)


def task(task_id="t1", agent="writer", **kwargs):
    return Task(
        task_id=task_id,
        title=f"Synthetic fixture {task_id}",
        assignee=agent,
        outputs=[f"outputs/{task_id}.md"],
        acceptance=[AcceptanceRule(type="file_exists", path=f"outputs/{task_id}.md")],
        **kwargs,
    )


def graph(*tasks, final=None):
    final = final or tasks[-1].task_id
    return TaskGraph(
        tasks=list(tasks),
        final_task_id=final,
        goal_acceptance=[AcceptanceRule(type="file_exists", path=f"{final}.md")],
    )


def config(**kwargs):
    return RunConfig(**{"memory_enabled": False, "write_memory": False, **kwargs})


def profile(agent_id="writer", tools=None, role="worker"):
    return AgentProfile(
        agent_id=agent_id,
        role=role,
        system_prompt="Use only the supplied synthetic fixtures.",
        tools=tools or [],
    )


def passed():
    return CriticResult(passed=True, checks_passed=1, checks_total=1)


def failed():
    return CriticResult(passed=False, checks_passed=0, checks_total=1, defects=["Missing section"])


class StubVerifier:
    """Scripted verdicts test the A/D interface; this is not a production evaluator."""

    def __init__(self, *responses):
        self.responses = deque(responses)
        self.calls = []

    async def check(self, rules, root):
        self.calls.append((rules, root))
        return self.responses.popleft() if self.responses else passed()


class ScriptedWorker:
    def __init__(self, root, actions=None):
        self.root = root
        self.actions = {key: deque(value) for key, value in (actions or {}).items()}
        self.calls = []
        self.released = []

    async def execute(self, task, context):
        self.calls.append((task, context.model_copy(deep=True)))
        actions = self.actions.get(task.task_id)
        if actions:
            action = actions.popleft()
            if callable(action):
                return await action(task, context)
            return action
        for output in task.outputs:
            path = self.root / "workspaces" / task.assignee / output
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"Synthetic output: {task.task_id}", encoding="utf-8")
        return AgentOutcome(kind="finished", summary="Synthetic fixture completed")

    def release(self, run_id, task_id):
        self.released.append((run_id, task_id))


class FakeMemory:
    def __init__(self):
        self.reads = []
        self.writes = []

    async def retrieve(self, agent_id, task):
        self.reads.append((agent_id, task.task_id))
        return "Synthetic remembered lesson"

    async def reflect(self, task, result):
        self.writes.append((task, result))


class RecordingObserver:
    def __init__(self):
        self.plans = []
        self.states = []
        self.handoffs = []
        self.decisions = []
        self.delegations = []

    async def plan_ready(self, run_id, graph):
        self.plans.append((run_id, graph))

    async def tasks_delegated(self, run_id, parent, children):
        self.delegations.append((parent, children))

    async def task_changed(self, run_id, task, result):
        self.states.append((task.task_id, result.status))

    async def artifacts_handed_off(self, run_id, task_id, inputs):
        self.handoffs.append((task_id, inputs))

    async def hitl_decided(self, run_id, request, decision):
        self.decisions.append(decision)


class ScriptedModel:
    def __init__(self, *responses):
        self.responses = deque(responses)
        self.calls = []

    async def complete(self, messages, tools, *, max_tokens):
        self.calls.append(([item.model_copy(deep=True) for item in messages], tools, max_tokens))
        response = self.responses.popleft()
        return ModelResponse(content=response) if isinstance(response, str) else response


class FixtureTools:
    """Write only explicit synthetic test fixture content into pytest's temporary root."""

    def __init__(self, root: Path):
        self.root = root
        self.calls = []

    async def execute(self, call, context):
        self.calls.append(call)
        path = self.root / "workspaces" / context.agent_id / call.arguments["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(call.arguments["text"], encoding="utf-8")
        return ToolResult(ok=True, data={"written": call.arguments["path"]})
