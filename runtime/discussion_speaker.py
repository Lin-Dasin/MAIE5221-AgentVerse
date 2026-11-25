"""One persona's model/tool loop, isolated from the task-graph runtime."""

import json
from collections.abc import Mapping, Sequence

from protocol.models import (
    AgentProfile,
    ChatMessage,
    DiscussionConfig,
    DiscussionStopReason,
    DiscussionTurn,
    ExecutionContext,
    ToolCall,
    ToolDefinition,
    ToolResult,
)
from protocol.ports import ModelClient, ToolExecutor
from runtime.client import ModelOutputLimitError
from runtime.discussion_budget import DiscussionBudgetExceeded
from runtime.discussion_context import (
    TokenCounter,
    count_tokens,
    estimate_tokens,
    excerpt,
    history_view,
    prompt_view,
)
from runtime.discussion_delivery_policy import (
    HANDOFF_OUTPUT_TOKENS,
    INSPECTION_CALL_LIMITS,
    PRODUCTION_TOOLS,
    call_limit,
    delivery_step,
    output_path,
)
from runtime.discussion_handoff import contains_tool_markup, fallback, report, report_prompt
from runtime.discussion_workflow import phase_instruction

MAX_TOOL_CALLS_PER_STEP = 16


class SpeakerStopped(RuntimeError):
    def __init__(self, reason: DiscussionStopReason):
        super().__init__(reason)
        self.reason = reason


async def _execute_tool(
    call: ToolCall,
    allowed: Mapping[str, ToolDefinition],
    executor: ToolExecutor,
    context: ExecutionContext,
) -> ToolResult:
    definition = allowed.get(call.name)
    if definition is None:
        return ToolResult(ok=False, error="Tool is not available to this speaker")
    if definition.requires_approval:
        return ToolResult(ok=False, error="Discussion has no permission approval channel")
    try:
        return await executor.execute(call, context)
    except Exception as exc:
        return ToolResult(ok=False, error=f"Tool execution failed: {type(exc).__name__}")


async def speak(
    *,
    profile: AgentProfile,
    config: DiscussionConfig,
    context: ExecutionContext,
    round_number: int,
    moderator: str,
    history: Sequence[ChatMessage],
    tool_definitions: Mapping[str, ToolDefinition],
    model: ModelClient,
    executor: ToolExecutor,
    remaining_tokens: int,
    max_steps: int,
    work_budget_tokens: int | None = None,
    token_counter: TokenCounter = estimate_tokens,
    progress: str = "",
    roster: Sequence[str] = (),
    max_search_calls: int = 2,
    delivery_mode: bool = False,
    repair: bool = False,
    required_artifacts: Sequence[str] = (),
    require_all_artifacts: bool = False,
) -> DiscussionTurn:
    allowed = {name: tool_definitions[name] for name in profile.tools}
    offered = [tool for tool in allowed.values() if not tool.requires_approval]
    base = [
        ChatMessage(
            role="system",
            content=(
                f"{profile.system_prompt}\n\nYou are {profile.agent_id} in a group discussion. "
                "Advance the current goal with concrete work: use permitted tools to research, "
                "write requested artifacts in outputs/, or test them. Inputs from earlier tasks "
                "are in inputs/ if supplied by the caller. Report actual results and paths. "
                "Tool data marked truncated_for_prompt is an excerpt; do not infer missing "
                "facts or claim to have reviewed a whole file from it. "
                "Do not repeat plans or treat prior-task work as the current assignment. "
                "Use reversible, explicitly stated assumptions for noncritical gaps; ask one "
                "focused question when human input is necessary. Never assume permission for "
                "critical actions. Keep public updates around 150 words, except when delivering "
                "the requested article or other substantive output. Respond to teammates, "
                "identify remaining blockers, and do not claim unperformed work or reveal "
                "private reasoning. Budget is finite; deliver usable work before further debate."
                f"\n{phase_instruction(round_number, config.max_rounds)}"
            ),
        ),
        ChatMessage(
            role="user",
            content=(
                f"Discussion round {round_number}; lead speaker: {moderator}; "
                f"maximum rounds: {config.max_rounds}.\n"
                f"Goal: {config.goal}\n"
                f"Team: {', '.join(roster) or profile.agent_id}.\n"
                f"Recorded artifact progress (not acceptance):\n{progress}\n"
                f"Shared conversation (bounded excerpts, oldest first):\n{history_view(history)}"
            ),
        ),
    ]
    calls: list[ToolCall] = []
    results: list[ToolResult] = []
    call_ids: set[str] = set()
    exchanges: list[list[ChatMessage]] = []
    used = 0
    work_budget = remaining_tokens if work_budget_tokens is None else work_budget_tokens
    search_calls = 0
    delivery_started: int | None = None
    provisional_reply: str | None = None
    length_failures = 0
    listing_cache: dict[str, ToolResult] = {}
    inspection_spent: dict[str, int] = {}
    managed = delivery_mode and any(tool.name in PRODUCTION_TOOLS for tool in offered)
    attempt_tokens_left = remaining_tokens
    if managed:
        # Fair turn ceiling within the attempt; the wrapper still meters every call.
        remaining_tokens = min(remaining_tokens, work_budget)
    report_cap = min(HANDOFF_OUTPUT_TOKENS, config.max_output_tokens)

    def snapshot(content: str) -> DiscussionTurn:
        return DiscussionTurn(
            round=round_number,
            speaker=profile.agent_id,
            content=content,
            tool_calls=calls,
            tool_results=results,
            tokens=used,
        )

    def budget_stop() -> DiscussionTurn:
        if managed and remaining_tokens < attempt_tokens_left and used < attempt_tokens_left:
            return fallback(snapshot("Contribution quota exhausted."), "contribution_budget")
        raise SpeakerStopped("token_budget")

    async def close() -> DiscussionTurn:
        turn = snapshot("No model report available.")
        try:
            return await report(
                turn,
                messages=report_prompt(turn, required_artifacts),
                model=model,
                token_counter=token_counter,
                token_limit=remaining_tokens,
                output_cap=report_cap,
            )
        except DiscussionBudgetExceeded as exc:
            raise SpeakerStopped("token_budget") from exc
        except TimeoutError as exc:
            raise SpeakerStopped("timeout") from exc
        except Exception as exc:
            raise SpeakerStopped("model_error") from exc

    invalid_text_retries = 0
    for step in range(1, max_steps + 1):
        if managed and step == max_steps:
            return await close()
        messages = prompt_view(base, exchanges, final=False)
        final_messages = [] if managed else prompt_view(base, exchanges, final=True)
        if managed:
            # Prompt compaction must not hide an earlier successful write behind
            # later reads. Receipts prove execution, not current file quality.
            writes = [
                {"path": call.arguments.get("path"), "ok": True}
                for call, result in zip(calls, results, strict=True)
                if call.name == "file_write" and result.ok
            ]
            receipt = ChatMessage(
                role="user",
                content="Current contribution file_write receipts (executed successfully at "
                "the time of the call; not acceptance or a guarantee the file is still valid): "
                + json.dumps(writes[-16:], ensure_ascii=False)
                + ". Use these recorded actions in the handoff. Do not claim no changes were "
                "made if a successful write is recorded. Report unresolved defects separately.",
            )
            messages.append(receipt)
        if length_failures:
            length_guidance = ChatMessage(
                role="user",
                content="Your previous response exceeded the per-call output limit and was "
                "discarded; none of its tool calls ran. Retry with a smaller contribution: "
                "write one small module/file at a time, avoid embedding a whole application "
                "in one tool call, and keep the final reply brief. Do not repeat oversized output.",
            )
            messages.append(length_guidance)
            if not managed:
                final_messages.insert(-1, length_guidance)
        active_tools = [
            tool for tool in offered if tool.name != "web_search" or search_calls < max_search_calls
        ]
        if provisional_reply:
            reminder = ChatMessage(
                role="user",
                content=(
                    f"Your tentative reply: {excerpt(provisional_reply, 1200)}\n"
                    "No successful file write via file_write has been recorded "
                    "in this contribution. If code_exec already created files, "
                    "inspect and report those actual artifacts. "
                    "Use the remaining opportunity to produce, inspect or repair the requested "
                    "deliverable in your role. For a text deliverable, save a useful draft with "
                    "file_write. Do not merely announce future work. If essential human input "
                    "is missing, report that blocker explicitly; never invent the answer."
                ),
            )
            messages.append(reminder)
            if not managed:
                final_messages.insert(-1, reminder)
        prompt_tokens = count_tokens(token_counter, messages, active_tools)
        output_cap = config.max_output_tokens
        if managed:
            closing_messages = report_prompt(snapshot("Pending"), required_artifacts)
            closing_reserve = (
                count_tokens(token_counter, closing_messages, []) + report_cap + 1000
            )
            policy = delivery_step(
                tools=active_tools,
                step=step,
                max_steps=max_steps,
                repair=repair,
                wrote=any(
                    result.ok
                    and call.name == "file_write"
                    and output_path(call.arguments.get("path")) is not None
                    and (
                        not required_artifacts
                        or output_path(call.arguments.get("path")) in required_artifacts
                    )
                    for call, result in zip(calls, results, strict=True)
                ),
                needs_production=bool(length_failures or provisional_reply or invalid_text_retries),
                inspection_spent=inspection_spent,
                available=remaining_tokens - used,
                prompt_tokens=prompt_tokens,
                closing_reserve=closing_reserve,
                output_cap=config.max_output_tokens,
                allow_search=any(tool.name == "web_search" for tool in active_tools)
                and (
                    search_calls == 0
                    or "No usable web_search results" in profile.system_prompt
                ),
            )
            if policy.phase == "report":
                return await close()
            finalizing = False
            active_tools = policy.tools
            output_cap = policy.output_cap
            messages.append(
                ChatMessage(
                    role="user",
                    content=policy.instruction
                    + " Direct file_write calls must save under outputs/. "
                    + (
                        "Verification reads reopen only after writing a declared target: "
                        + json.dumps(list(required_artifacts))
                        + ". Helper files alone do not count as the required repair."
                        if required_artifacts
                        else ""
                    ),
                )
            )
            prompt_tokens = count_tokens(token_counter, messages, active_tools)
            # Recount after policy guidance; preserve the actual closing request.
            output_cap = min(
                output_cap, remaining_tokens - used - prompt_tokens - closing_reserve
            )
            if output_cap <= 0:
                return await close()
        else:
            final_tokens = count_tokens(token_counter, final_messages, [])
            # Budget for a text response after a tool round, including a bounded
            # result view. This is a soft share; the global budget is still the cap.
            closing_reserve = final_tokens + min(512, config.max_output_tokens) + 2000
            pressured = used + prompt_tokens + config.max_output_tokens + closing_reserve > min(
                work_budget, remaining_tokens
            )
            if pressured and delivery_started is None:
                delivery_started = step
            finalizing = step == max_steps or (
                delivery_started is not None
                and (
                    step > delivery_started
                    or any(
                        result.ok and call.name == "file_write"
                        for call, result in zip(calls, results, strict=True)
                    )
                )
            )
            if delivery_started is not None and not finalizing:
                # Keep one bounded opportunity for writing/checking, rather
                # than dropping all tools as soon as exploration is too expensive.
                active_tools = [tool for tool in active_tools if tool.name != "web_search"]
                messages.append(
                    ChatMessage(
                        role="user",
                        content="Research allowance has ended. Use this last tool step "
                        "to write or check the current deliverable "
                        "(batch related calls if needed), "
                        "then report actual results. "
                        "Preserve evidence limitations; do not start another research loop.",
                    )
                )
                prompt_tokens = count_tokens(token_counter, messages, active_tools)
                if (
                    used + prompt_tokens + config.max_output_tokens + final_tokens + 512
                    > remaining_tokens
                ):
                    finalizing = True
            if finalizing:
                messages, active_tools, prompt_tokens = final_messages, [], final_tokens
        output_limit = min(output_cap, remaining_tokens - used - prompt_tokens)
        # When a normal tool request no longer fits, try the smaller final
        # response once. The counter is an estimate, not a billing guarantee.
        if output_limit <= 0 and not finalizing and not managed:
            finalizing = True
            messages, active_tools, prompt_tokens = final_messages, [], final_tokens
            output_limit = min(output_cap, remaining_tokens - used - prompt_tokens)
        if output_limit <= 0:
            return budget_stop()
        try:
            response = await model.complete(
                messages.copy(), active_tools.copy(), max_tokens=output_limit
            )
        except DiscussionBudgetExceeded as exc:
            raise SpeakerStopped("token_budget") from exc
        except ModelOutputLimitError as exc:
            used += exc.tokens or (prompt_tokens + output_limit)
            if used >= remaining_tokens:
                return budget_stop()
            length_failures += 1
            if length_failures > 1 or step == max_steps:
                if managed:
                    return fallback(snapshot("Work response was discarded."), "work_truncated")
                raise SpeakerStopped("model_error") from exc
            continue
        except TimeoutError as exc:
            raise SpeakerStopped("timeout") from exc
        except Exception as exc:
            raise SpeakerStopped("model_error") from exc
        used += response.tokens or (prompt_tokens + output_limit)
        if used > remaining_tokens:
            return budget_stop()

        if not response.tool_calls:
            if managed and contains_tool_markup(response.content):
                if finalizing or invalid_text_retries or step >= max_steps - 1:
                    raise SpeakerStopped("invalid_model_output")
                invalid_text_retries += 1
                exchanges.append(
                    [
                        ChatMessage(
                            role="user",
                            content="Your plain-text tool markup was not executed. "
                            "Use the structured tool_calls channel for the actual write, "
                            "or report a blocker in plain text. Never claim that markup ran.",
                        )
                    ]
                )
                continue
            if not response.content.strip():
                raise SpeakerStopped("invalid_model_output")
            written = {
                path
                for call, result in zip(calls, results, strict=True)
                if result.ok
                and call.name == "file_write"
                and (path := output_path(call.arguments.get("path"))) is not None
            }
            if managed and required_artifacts:
                performed_work = (
                    all(path in written for path in required_artifacts)
                    if require_all_artifacts
                    else bool(written.intersection(required_artifacts))
                )
            else:
                performed_work = any(
                    result.ok and call.name == "file_write"
                    for call, result in zip(calls, results, strict=True)
                )
            if (
                not finalizing
                and step < max_steps - 1
                and provisional_reply is None
                and not performed_work
                and any(tool.name == "file_write" for tool in active_tools)
            ):
                provisional_reply = response.content
                continue
            return snapshot(response.content)
        if finalizing:
            raise SpeakerStopped("step_limit")
        ids = {call.call_id for call in response.tool_calls}
        if (
            len(response.tool_calls) > MAX_TOOL_CALLS_PER_STEP
            or len(ids) != len(response.tool_calls)
            or ids & call_ids
        ):
            raise SpeakerStopped("invalid_model_output")
        if used == remaining_tokens:
            return budget_stop()
        exchange = [
            ChatMessage(
                role="assistant",
                content=response.content,
                tool_calls=response.tool_calls,
                reasoning_content=response.reasoning_content,
            )
        ]
        for call in response.tool_calls:
            listing_key = json.dumps(call.arguments, sort_keys=True, ensure_ascii=False)
            active_names = {tool.name for tool in active_tools}
            if (
                managed
                and call.name == "file_write"
                and output_path(call.arguments.get("path")) is None
            ):
                result = ToolResult(
                    ok=False,
                    error="Managed delivery writes must save under outputs/. "
                    "Do not create unrelated placeholders in inputs/. Repair the declared target.",
                )
            elif managed and (
                call.name not in active_names
                or inspection_spent.get(call.name, 0) >= call_limit(call.name, policy.phase)
            ):
                result = ToolResult(
                    ok=False,
                    error="Tool unavailable in this delivery phase or inspection quota "
                    "exhausted. Persist the repair using offered tools or report the blocker.",
                )
            elif call.name == "file_list" and listing_key in listing_cache:
                result = listing_cache[listing_key].model_copy(deep=True)
                result.meta["reused_listing"] = True
            elif call.name == "web_search" and (
                search_calls >= max_search_calls
                or (delivery_started is not None and search_calls > 0)
            ):
                result = ToolResult(
                    ok=False,
                    error="Search allowance exhausted. Use existing evidence, "
                    "write/check the deliverable and disclose remaining uncertainty.",
                )
            else:
                if call.name in INSPECTION_CALL_LIMITS:
                    inspection_spent[call.name] = inspection_spent.get(call.name, 0) + 1
                if call.name == "web_search":
                    search_calls += 1
                result = await _execute_tool(call, allowed, executor, context)
                if call.name == "file_list" and result.ok:
                    listing_cache[listing_key] = result.model_copy(deep=True)
            # Even a failed mutating call may have partially changed the workspace.
            if call.name not in {"file_list", "file_read", "web_search"}:
                listing_cache.clear()
            calls.append(call)
            results.append(result)
            exchange.append(
                ChatMessage(
                    role="tool", tool_call_id=call.call_id, content=result.model_dump_json()
                )
            )
        exchanges.append(exchange)
        call_ids.update(ids)
    raise SpeakerStopped("step_limit")
