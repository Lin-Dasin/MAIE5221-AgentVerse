"""Approval policies are supplied by the application, never invented by a model."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping

from protocol.models import HITLDecision, HITLRequest


class PolicyApprover:
    def __init__(
        self,
        allowed_actions: frozenset[str] = frozenset(),
        clarification_answers: Mapping[str, str] | None = None,
    ):
        self.allowed_actions = allowed_actions
        self.clarification_answers = dict(clarification_answers or {})

    async def decide(self, request: HITLRequest) -> HITLDecision:
        if request.kind == "clarification" and request.action in self.clarification_answers:
            answer = self.clarification_answers[request.action]
            return HITLDecision(
                request_id=request.request_id,
                approved=True,
                answer=answer,
                assumptions=[f"Configured default for {request.action}: {answer}"],
                source="policy",
            )
        approved = request.kind == "permission" and request.action in self.allowed_actions
        return HITLDecision(
            request_id=request.request_id,
            approved=approved,
            answer="Allowed by configured policy" if approved else "No applicable approval rule",
            source="policy",
        )


class InteractiveApprover:
    """D supplies the UI callback; A supplies the bounded pause/resume contract."""

    def __init__(
        self, ask: Callable[[HITLRequest], Awaitable[HITLDecision]], timeout_sec: float = 60
    ):
        if timeout_sec <= 0:
            raise ValueError("approval timeout must be positive")
        self.ask = ask
        self.timeout_sec = timeout_sec

    async def decide(self, request: HITLRequest) -> HITLDecision:
        try:
            async with asyncio.timeout(self.timeout_sec):
                decision = await self.ask(request)
        except TimeoutError:
            return HITLDecision(
                request_id=request.request_id,
                approved=False,
                answer="Human response timed out",
                source="timeout",
            )
        if decision.request_id != request.request_id:
            raise ValueError("approval response belongs to a different request")
        return decision
