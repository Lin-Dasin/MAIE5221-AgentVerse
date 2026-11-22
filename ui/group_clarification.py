"""Bridge a waiting task and a reply submitted by the group run's requester."""

import asyncio

from bus.messages import MessageBus
from protocol.models import HITLDecision, HITLRequest


class GroupClarification:
    """Poll the local message store while the shared approver enforces its deadline."""

    def __init__(
        self,
        bus: MessageBus,
        conversation: str,
        requester: str,
        notifier: str,
        run_id: str,
        *,
        timeout_sec: float,
    ) -> None:
        self.bus = bus
        self.conversation = conversation
        self.requester = requester
        self.notifier = notifier
        self.run_id = run_id
        self.timeout_sec = timeout_sec

    async def __call__(self, request: HITLRequest) -> HITLDecision:
        if request.kind != "clarification":
            return HITLDecision(
                request_id=request.request_id,
                approved=False,
                answer="Group replies support clarification only",
                source="policy",
            )
        request_id = request.request_id
        self.bus.request_clarification(
            self.conversation,
            self.notifier,
            self.requester,
            run_id=self.run_id,
            request_id=request_id,
            task_id=request.task_id,
            question=request.question,
            timeout_sec=self.timeout_sec,
        )
        print(f"Clarification pending in {self.conversation}: {request_id}", flush=True)
        try:
            while True:
                answer = self.bus.get_clarification_answer(
                    self.conversation, self.run_id, request_id
                )
                if answer is not None:
                    return HITLDecision(
                        request_id=request_id, approved=True, answer=answer, source="human"
                    )
                await asyncio.sleep(0.1)
        finally:
            self.bus.close_clarification(self.conversation, self.run_id, request_id)
