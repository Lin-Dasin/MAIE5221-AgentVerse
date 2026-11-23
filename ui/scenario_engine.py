"""Connect Mid Test groups to the discussion engine and prepare files for review."""

import json
from collections import Counter
from collections.abc import Callable
from importlib import import_module
from importlib.util import find_spec
from typing import TYPE_CHECKING

from harness.scenario import INSTRUCTOR
from orchestrator.artifacts import copy_file, inside
from protocol.models import ChatMessage
from protocol.ports import ModelClient
from runtime.client import DeepSeekClient
from sandbox.executor import SandboxToolExecutor
from tools import registry
from ui.scenario import (
    NOTIFIER,
    TaskEngine,
    TaskRequest,
    _ordinary_files,
    _require_unlinked,
    file_sha256,
)

if TYPE_CHECKING:
    from protocol.models import DiscussionTurn


class _HumanInput:
    def __init__(self, poll: Callable[[], str | None]) -> None:
        self.poll = poll

    async def next_message(self) -> ChatMessage | None:
        text = self.poll()
        return ChatMessage(role="user", content=text) if text is not None else None


def _history(request: TaskRequest) -> list[ChatMessage]:
    messages = []
    for event in request.history:
        if event.kind != "message":
            continue
        if event.sender == INSTRUCTOR:
            messages.append(ChatMessage(role="user", content=event.text))
        elif event.sender == NOTIFIER or event.sender in request.profiles:
            messages.append(ChatMessage(role="assistant", content=f"[{event.sender}] {event.text}"))
    paths = ", ".join(f"inputs/{path}" for path in request.inputs) or "none"
    messages.append(
        ChatMessage(
            role="assistant",
            content=(
                "[workspace] Save files you want others to inspect under outputs/. "
                "After your turn, files in outputs/ become available to other roles at "
                "inputs/shared/<author>/<path relative to outputs/>. Mention the exact "
                "shared paths in your public message. The accepted files carried forward "
                f"from the previous task are at these exact paths: {paths}. If files are listed, "
                "roles with file_read should consult them early and carry forward useful context. "
                "Collect evidence for the current task independently."
            ),
        )
    )
    return messages


def _share_outputs(request: TaskRequest, executor: SandboxToolExecutor, speaker: str) -> None:
    output_root = executor.workspace(speaker) / "outputs"
    _require_unlinked(output_root)
    for source in _ordinary_files(output_root):
        relative = source.relative_to(output_root).as_posix()
        for peer in request.profiles:
            if peer != speaker:
                destination = inside(
                    executor.workspace(peer), f"inputs/shared/{speaker}/{relative}"
                )
                copy_file(source, destination)


def _stage_candidates(request: TaskRequest, executor: SandboxToolExecutor) -> dict[str, str]:
    candidates = {}
    for speaker in request.profiles:
        output_root = executor.workspace(speaker) / "outputs"
        _require_unlinked(output_root)
        for source in _ordinary_files(output_root):
            path = f"outputs/{speaker}/{source.relative_to(output_root).as_posix()}"
            destination = inside(request.workspace, path)
            copy_file(source, destination)
            candidates[path] = file_sha256(destination)
    return candidates


def discussion_engine(
    model: ModelClient,
    *,
    max_rounds: int = 4,
    max_task_tokens: int = 60_000,
    max_output_tokens: int = 2048,
) -> TaskEngine:
    """Run open discussion, retaining candidate files for explicit human acceptance."""
    from protocol.models import DiscussionConfig

    discussion = import_module("orchestrator.discussion")

    async def run(
        request: TaskRequest, human: Callable[[], str | None], emit: Callable[[str, str], None]
    ) -> None:
        speakers = list(request.profiles)
        config = DiscussionConfig(
            conversation_id=request.conversation_id,
            run_id=request.run_id,
            task_id=request.task.task_id,
            goal=request.task.text,
            speakers=speakers,
            moderator=speakers[0],
            max_rounds=max_rounds,
            max_task_tokens=max_task_tokens,
            max_output_tokens=max_output_tokens,
        )
        tools = registry.build()
        required = {name for profile in request.profiles.values() for name in profile.tools}
        if unknown := required - tools.keys():
            raise ValueError(f"Unknown scenario tools: {', '.join(sorted(unknown))}")
        executor = SandboxToolExecutor(
            request.workspace, {name: tools[name] for name in sorted(required)}
        )
        for speaker in speakers:
            workspace = executor.workspace(speaker)
            (workspace / "outputs").mkdir(parents=True)
            for relative in request.inputs:
                path = f"inputs/{relative}"
                copy_file(inside(request.workspace, path), inside(workspace, path))

        async def publish(turn: "DiscussionTurn") -> None:
            _share_outputs(request, executor, turn.speaker)
            emit(turn.speaker, turn.content)

        result = await discussion.RoundRobinDiscussionEngine(executor.tool_definitions()).run(
            config,
            profiles=request.profiles,
            history=_history(request),
            model=model,
            executor=executor,
            human=_HumanInput(human),
            emit=publish,
        )
        candidates = _stage_candidates(request, executor)
        counts: Counter[str] = Counter()
        prior_reads = set()
        prior_files = {f"inputs/{path}" for path in request.inputs}
        for turn in result.turns:
            for call, response in zip(turn.tool_calls, turn.tool_results, strict=True):
                counts[f"{call.name}:{'ok' if response.ok else 'failed'}"] += 1
                if not response.ok or not isinstance(response.data, dict):
                    continue
                if call.name == "web_search" and response.data.get("provider") == "tavily":
                    if response.data.get("results"):
                        counts["tavily:nonempty"] += 1
                if call.name == "file_read" and (path := response.data.get("path")) in prior_files:
                    prior_reads.add((turn.speaker, path))
        summary = {
            "stop_reason": result.stopped_reason,
            "turns": len(result.turns),
            "reported_tokens": sum(turn.tokens for turn in result.turns),
            "tool_counts": dict(counts),
            "prior_task_reads": [dict(speaker=s, path=p) for s, p in sorted(prior_reads)],
            "candidates": candidates,
        }
        location = request.workspace / "discussion-summary.json"
        location.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"Discussion stopped: {result.stopped_reason}. Attempt: {request.run_id}")
        print(f"Discussion summary: {location}")
        if candidates and result.stopped_reason == "max_rounds":
            print("Candidates for human review:")
            for path in candidates:
                print(f"  {path}")

    return run


def make_live_engine(
    *, max_rounds: int, max_task_tokens: int, max_output_tokens: int
) -> TaskEngine:
    """Construct the configured DeepSeek client inside the running event loop."""
    if find_spec("orchestrator.discussion") is None:
        raise RuntimeError("Mid Test live execution requires Role A's discussion engine (PR #40)")
    if min(max_rounds, max_task_tokens, max_output_tokens) < 1:
        raise ValueError("Mid Test limits must be positive integers")

    async def run(
        request: TaskRequest, human: Callable[[], str | None], emit: Callable[[str, str], None]
    ) -> None:
        client = DeepSeekClient.from_environment()
        try:
            engine = discussion_engine(
                client,
                max_rounds=max_rounds,
                max_task_tokens=max_task_tokens,
                max_output_tokens=max_output_tokens,
            )
            await engine(request, human, emit)
        finally:
            await client.aclose()

    return run
