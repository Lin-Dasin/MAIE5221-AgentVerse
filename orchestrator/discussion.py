"""Standalone group discussion entry point."""

import asyncio
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import copy

from protocol.models import (
    AgentProfile,
    ChatMessage,
    DiscussionConfig,
    DiscussionResult,
    DiscussionStopReason,
    DiscussionTurn,
    ExecutionContext,
    RunConfig,
    ToolDefinition,
)
from protocol.ports import HumanInput, ModelClient, ToolExecutor
from runtime.discussion_context import TokenCounter, estimate_tokens
from runtime.discussion_speaker import SpeakerStopped, speak
from runtime.discussion_workflow import progress_view

HUMAN_POLL_TIMEOUT_SEC = 1


class RoundRobinDiscussionEngine:
    """Each persona speaks once per round, starting with the rotating moderator."""

    def __init__(
        self,
        tool_definitions: Sequence[ToolDefinition] = (),
        *,
        max_steps_per_turn: int = 8,
        turn_timeout_sec: float = 120,
        prompt_token_counter: TokenCounter = estimate_tokens,
        max_search_calls_per_turn: int = 2,
    ) -> None:
        self._tools = {tool.name: tool.model_copy(deep=True) for tool in tool_definitions}
        if len(self._tools) != len(tool_definitions):
            raise ValueError("discussion tool definitions must have unique names")
        if not 1 <= max_steps_per_turn <= 100:
            raise ValueError("max_steps_per_turn must be between 1 and 100")
        if not math.isfinite(turn_timeout_sec) or turn_timeout_sec <= 0:
            raise ValueError("turn_timeout_sec must be finite and positive")
        self._max_steps = max_steps_per_turn
        self._turn_timeout = turn_timeout_sec
        self._token_counter = prompt_token_counter
        if isinstance(max_search_calls_per_turn, bool) or not isinstance(
            max_search_calls_per_turn, int
        ):
            raise ValueError("max_search_calls_per_turn must be an integer")
        if not 0 <= max_search_calls_per_turn <= 16:
            raise ValueError("max_search_calls_per_turn must be between 0 and 16")
        self._max_search = max_search_calls_per_turn
        self._delivery_mode = False
        self._repair = False
        self._delivery_artifacts: dict[str, tuple[str, ...]] = {}

    def for_delivery(
        self, *, repair: bool = False, artifacts: Mapping[str, Sequence[str]] | None = None
    ) -> "RoundRobinDiscussionEngine":
        """Use a per-run policy without mutating an engine shared by other callers."""
        engine = copy(self)
        engine._delivery_mode = True
        engine._repair = repair
        engine._delivery_artifacts = {
            owner: tuple(paths) for owner, paths in (artifacts or {}).items()
        }
        return engine

    async def run(
        self,
        config: DiscussionConfig,
        *,
        profiles: Mapping[str, AgentProfile],
        history: Sequence[ChatMessage],
        model: ModelClient,
        executor: ToolExecutor,
        human: HumanInput,
        emit: Callable[[DiscussionTurn], Awaitable[None]] | None,
    ) -> DiscussionResult:
        config = DiscussionConfig.model_validate(config.model_dump())
        participants: dict[str, AgentProfile] = {}
        for speaker in config.speakers:
            profile = profiles.get(speaker)
            if profile is None or profile.agent_id != speaker or profile.role != "worker":
                raise ValueError(f"'{speaker}' must identify a registered worker persona")
            if unknown := set(profile.tools) - self._tools.keys():
                raise ValueError(f"unknown discussion tools for '{speaker}': {sorted(unknown)}")
            participants[speaker] = profile.model_copy(deep=True)

        # The public transcript is only text. Never replay tool packets, private
        # reasoning or caller-supplied system messages in a new persona's turn.
        public_history = [
            ChatMessage(role=message.role, content=message.content)
            for message in history
            if message.role in ("user", "assistant")
            and message.content.strip()
            and not message.tool_calls
            and message.tool_call_id is None
        ]
        turns: list[DiscussionTurn] = []
        remaining = config.max_task_tokens
        moderator_index = config.speakers.index(config.moderator)
        run_config = RunConfig(
            run_id=config.run_id,
            memory_enabled=False,
            write_memory=False,
            max_agent_steps=self._max_steps,
            max_task_tokens=config.max_task_tokens,
            max_output_tokens=config.max_output_tokens,
            task_timeout_sec=self._turn_timeout,
        )

        def finish(reason: DiscussionStopReason) -> DiscussionResult:
            return DiscussionResult(
                conversation_id=config.conversation_id,
                run_id=config.run_id,
                turns=turns,
                stopped_reason=reason,
            )

        for round_number in range(1, config.max_rounds + 1):
            order = config.speakers[moderator_index:] + config.speakers[:moderator_index]
            round_start = remaining
            # Keep a soft reserve for later review/delivery rounds.
            round_allowance = (
                remaining
                if self._delivery_mode or round_number == config.max_rounds
                else remaining * 2 // 5
            )
            for position, speaker in enumerate(order):
                context = ExecutionContext(
                    config=run_config, task_id=config.task_id, agent_id=speaker, attempt=1
                )
                try:
                    async with asyncio.timeout(self._turn_timeout):
                        turn = await speak(
                            profile=participants[speaker],
                            config=config,
                            context=context,
                            round_number=round_number,
                            moderator=order[0],
                            history=public_history,
                            tool_definitions=self._tools,
                            model=model,
                            executor=executor,
                            remaining_tokens=remaining,
                            max_steps=self._max_steps,
                            work_budget_tokens=max(0, round_allowance - (round_start - remaining))
                            // (len(order) - position),
                            token_counter=self._token_counter,
                            progress=progress_view(turns),
                            roster=config.speakers,
                            max_search_calls=self._max_search,
                            delivery_mode=self._delivery_mode,
                            repair=self._repair,
                            required_artifacts=self._delivery_artifacts.get(speaker, ()),
                        )
                except SpeakerStopped as exc:
                    return finish(exc.reason)
                except TimeoutError:
                    return finish("timeout")
                remaining -= turn.tokens
                turns.append(turn)
                public_history.append(
                    ChatMessage(role="assistant", content=f"[{turn.speaker}] {turn.content}")
                )
                if emit is not None:
                    try:
                        async with asyncio.timeout(self._turn_timeout):
                            await emit(turn.model_copy(deep=True))
                    except TimeoutError:
                        return finish("timeout")

            if round_number < config.max_rounds:
                if not remaining:
                    return finish("token_budget")
                try:
                    async with asyncio.timeout(HUMAN_POLL_TIMEOUT_SEC):
                        message = await human.next_message()
                except TimeoutError:
                    return finish("timeout")
                if message is not None:
                    if message.role != "user" or message.tool_calls or message.tool_call_id:
                        raise ValueError("HumanInput must return a plain user message")
                    if message.content.strip():
                        public_history.append(ChatMessage(role="user", content=message.content))
            moderator_index = (moderator_index + 1) % len(config.speakers)

        return finish("max_rounds")
