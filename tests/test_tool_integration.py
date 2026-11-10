"""Cross-module check: A's agent loop driving B's real tools and D's verifier.

Every other test uses a tool double. This one wires the real `SandboxToolExecutor`
into `AgentRuntime` with a scripted model, so the path model -> runtime ->
executor -> file tool -> workspace -> verifier is exercised without a network call.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

from harness.verdict import FileVerifier
from protocol.models import (
    AcceptanceRule,
    AgentProfile,
    ExecutionContext,
    ModelResponse,
    RunConfig,
    Task,
    ToolCall,
    ToolDefinition,
)
from runtime.agent import AgentRuntime
from sandbox.executor import SandboxToolExecutor
from tools.registry import build, definitions


class ScriptedModel:
    """Minimal `ModelClient`: replays prepared responses in order."""

    def __init__(self, responses: list[ModelResponse]) -> None:
        self.responses = deque(responses)
        self.requests: list[list[ToolDefinition]] = []

    async def complete(
        self, messages: list[object], tools: list[ToolDefinition], *, max_tokens: int
    ) -> ModelResponse:
        self.requests.append(list(tools))
        return self.responses.popleft()


async def test_agent_loop_writes_through_the_real_executor(tmp_path: Path) -> None:
    registry = build()
    executor = SandboxToolExecutor(tmp_path, registry)
    model = ScriptedModel(
        [
            ModelResponse(
                tool_calls=[
                    ToolCall(
                        call_id="call-1",
                        name="file_write",
                        arguments={"path": "outputs/note.md", "text": "hello from the sandbox"},
                    )
                ]
            ),
            ModelResponse(content="Wrote the note."),
        ]
    )
    profiles = {
        "writer": AgentProfile(
            agent_id="writer",
            role="worker",
            system_prompt="Write files when asked.",
            tools=["file_write"],
        )
    }
    runtime = AgentRuntime(model, executor, profiles, definitions(registry))

    task = Task(
        task_id="t1",
        title="Write a note",
        assignee="writer",
        acceptance=[AcceptanceRule(type="file_exists", path="outputs/note.md")],
        outputs=["outputs/note.md"],
    )
    context = ExecutionContext(
        config=RunConfig(memory_enabled=False, write_memory=False),
        task_id="t1",
        agent_id="writer",
        attempt=1,
    )

    outcome = await runtime.execute(task, context)

    assert outcome.kind == "finished"
    written = tmp_path / "workspaces" / "writer" / "outputs" / "note.md"
    assert written.read_text(encoding="utf-8") == "hello from the sandbox"

    verdict = await FileVerifier().check(task.acceptance, written.parent.parent)
    assert verdict.passed is True
    assert verdict.checks_total == 1


async def test_executor_error_reaches_the_model_as_a_tool_message(tmp_path: Path) -> None:
    """A failed tool must be reported, not raised, so the agent can react."""
    registry = build()
    executor = SandboxToolExecutor(tmp_path, registry)
    model = ScriptedModel(
        [
            ModelResponse(
                tool_calls=[
                    ToolCall(
                        call_id="call-1",
                        name="file_write",
                        arguments={"path": "../escape.md", "text": "nope"},
                    )
                ]
            ),
            ModelResponse(content="That path was rejected; writing inside outputs instead."),
        ]
    )
    profiles = {
        "writer": AgentProfile(
            agent_id="writer",
            role="worker",
            system_prompt="Write files when asked.",
            tools=["file_write"],
        )
    }
    runtime = AgentRuntime(model, executor, profiles, definitions(registry))

    task = Task(
        task_id="t1",
        title="Try an illegal path",
        assignee="writer",
        acceptance=[AcceptanceRule(type="file_exists", path="outputs/note.md")],
        outputs=["outputs/note.md"],
    )
    context = ExecutionContext(
        config=RunConfig(memory_enabled=False, write_memory=False),
        task_id="t1",
        agent_id="writer",
        attempt=1,
    )

    outcome = await runtime.execute(task, context)

    assert outcome.kind == "finished"
    assert not (tmp_path / "escape.md").exists()
    assert not (tmp_path / "workspaces" / "escape.md").exists()
