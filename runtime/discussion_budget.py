"""Shared usage across draft/rework, including calls in interrupted contributions."""

from protocol.models import ChatMessage, ModelResponse, ToolDefinition
from protocol.ports import ModelClient
from runtime.client import ModelOutputLimitError
from runtime.discussion_context import TokenCounter, count_tokens


class DiscussionBudgetExceeded(RuntimeError):
    pass


class DiscussionBudget:
    def __init__(self, model: ModelClient, limit: int, counter: TokenCounter):
        self.model = model
        self.limit = limit
        self.counter = counter
        self.tokens = 0
        self.calls = 0

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.tokens)

    async def complete(
        self, messages: list[ChatMessage], tools: list[ToolDefinition], *, max_tokens: int
    ) -> ModelResponse:
        reservation = count_tokens(self.counter, messages, tools) + max_tokens
        if reservation > self.remaining:
            raise DiscussionBudgetExceeded("delivery token budget exhausted")
        self.calls += 1
        charged = reservation
        try:
            response = await self.model.complete(messages, tools, max_tokens=max_tokens)
            charged = response.tokens or reservation
        except ModelOutputLimitError as exc:
            charged = exc.tokens or reservation
            raise
        finally:
            # Includes timeouts/cancellation/errors with unknown provider usage.
            self.tokens += charged
        if self.tokens > self.limit:
            # Do not dispatch tools from a response exceeding the shared budget.
            raise DiscussionBudgetExceeded("provider usage exceeded delivery budget estimate")
        return response
