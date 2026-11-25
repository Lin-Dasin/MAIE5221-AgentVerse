"""Short, tool-free closing reports for managed delivery contributions."""

from collections.abc import Sequence

from protocol.delivery import DeliveryHandoffTurn, HandoffIssue
from protocol.models import ChatMessage, DiscussionTurn
from protocol.ports import ModelClient
from runtime.client import ModelOutputLimitError
from runtime.discussion_context import TokenCounter, count_tokens
from runtime.discussion_workflow import progress_view


def fallback(turn: DiscussionTurn, issue: HandoffIssue) -> DeliveryHandoffTurn:
    return DeliveryHandoffTurn(
        **(
            turn.model_dump()
            | {
                "content": f"Automatic handoff ({issue}); this contribution did not finish. "
                "Recorded operations only; no acceptance verdict. "
                "Check the actual files and remaining requirements.\n"
                + progress_view([turn]),
                "handoff_issue": issue,
            }
        )
    )


def contains_tool_markup(text: str) -> bool:
    return "<" in text and "DSML" in text.upper()


HITL_MARKER = "[HITL]"


def status_footer(turn: DiscussionTurn) -> str:
    """Deterministic verification note derived only from recorded tool calls."""
    last_write = -1
    checked = False
    for index, (call, result) in enumerate(zip(turn.tool_calls, turn.tool_results, strict=True)):
        if call.name == "file_write" and result.ok:
            last_write = index
            checked = False
        elif (
            call.name == "code_exec"
            and result.ok
            and isinstance(result.data, dict)
            and result.data.get("exit_code") == 0
        ):
            checked = True
    if checked:
        return "[Verified: code_exec exit 0 after the last write.]"
    if last_write >= 0:
        return "[Unverified: no successful code_exec after the last write.]"
    return "[Unverified: no execution check was recorded.]"


def report_prompt(turn: DiscussionTurn, artifacts: Sequence[str]) -> list[ChatMessage]:
    return [
        ChatMessage(
            role="system",
            content=(
                "Write a professional handoff for teammates and the human in 150-250 characters "
                "(or the equivalent in Chinese). Use these labels: Decision: respond to upstream "
                "and state the key decision and its reason; Artifact & interface: saved paths plus "
                "the key interfaces, conclusions or gaps downstream must know; Next request: name "
                "who should act next and on what; Honest status: one sentence strictly based "
                "on the recorded operations, never claiming tests or checks that were not "
                "recorded. If an "
                "ambiguity, irreversible action or risky assumption needs the human, add one line "
                f"starting with {HITL_MARKER}: state a reversible assumption and one focused "
                "question. Otherwise omit it. No tools, no tool markup, no deliverable paste and "
                "no acceptance verdict."
            ),
        ),
        ChatMessage(
            role="user",
            content=f"Member: {turn.speaker}\nExpected paths (not proof of delivery): "
            f"{', '.join(artifacts[:8])}\nRecorded operations:\n{progress_view([turn])}",
        ),
    ]


async def report(
    turn: DiscussionTurn,
    *,
    messages: list[ChatMessage],
    model: ModelClient,
    token_counter: TokenCounter,
    token_limit: int,
    output_cap: int,
) -> DiscussionTurn:
    """At most one text-only retry; never expose or dispatch a rejected response."""
    issue: HandoffIssue = "contribution_budget"
    for attempt in range(2):
        prompt_tokens = count_tokens(token_counter, messages, [])
        output_limit = min(output_cap, token_limit - turn.tokens - prompt_tokens)
        if output_limit <= 0:
            break
        try:
            response = await model.complete(messages.copy(), [], max_tokens=output_limit)
        except ModelOutputLimitError as exc:
            turn.tokens += exc.tokens or (prompt_tokens + output_limit)
            issue = "report_truncated"
            if attempt == 0:
                messages = [
                    *messages,
                    ChatMessage(role="user", content="Previous report was cut off. Use one line."),
                ]
            continue
        turn.tokens += response.tokens or (prompt_tokens + output_limit)
        if turn.tokens > token_limit:
            issue = "contribution_budget"
        elif (
            response.tool_calls
            or not response.content.strip()
            or contains_tool_markup(response.content)
        ):
            issue = "invalid_report"
        else:
            turn.content = response.content.strip() + "\n\n" + status_footer(turn)
            return turn
        break
    return fallback(turn, issue)
