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


def report_prompt(turn: DiscussionTurn, artifacts: Sequence[str]) -> list[ChatMessage]:
    return [
        ChatMessage(
            role="system",
            content="Write a handoff only, at most three short bullets: saved paths, checks "
            "actually recorded, and any work still unverified. No tools or tool markup. Do "
            "not paste the deliverable, invent findings, or decide acceptance.",
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
            turn.content = response.content
            return turn
        break
    return fallback(turn, issue)
