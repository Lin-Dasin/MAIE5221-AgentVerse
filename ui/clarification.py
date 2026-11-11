"""Minimal terminal clarification callback for A's InteractiveApprover."""

import asyncio
import os
import sys
import threading
from collections.abc import Callable
from typing import Literal, TextIO

from protocol.models import HITLDecision, HITLRequest


class TerminalClarification:
    """Read one answer without blocking asyncio or joining stdin at shutdown.

    Reuse one instance for a terminal. A cancelled/timed-out read cannot be
    forcibly stopped; until it returns, further requests are declined. Its late
    answer is discarded rather than being applied to another request.
    """

    def __init__(self, reader: Callable[[str], str] | None = None):
        self._reader = reader
        self._stdin: TextIO | None = None
        self._read_lock = threading.Lock()

    def _read_terminal(self, prompt: str) -> str:
        # input() can hold sys.stdin's lock while waiting, which can block Python
        # shutdown even in a daemon thread. Give this reader its own text stream.
        print(prompt, end="", flush=True)
        if self._stdin is None:
            self._stdin = os.fdopen(
                os.dup(sys.stdin.fileno()),
                "r",
                encoding=sys.stdin.encoding or "utf-8",
                errors=sys.stdin.errors or "strict",
            )
        answer = self._stdin.readline()
        if not answer:
            self._stdin.close()
            self._stdin = None
            raise EOFError
        return answer

    async def __call__(self, request: HITLRequest) -> HITLDecision:
        if request.kind != "clarification":
            return self._decision(
                request, False, "This terminal accepts clarification only", source="policy"
            )
        if not self._read_lock.acquire(blocking=False):
            return self._decision(
                request, False, "A previous terminal input is still pending", source="policy"
            )

        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()

        def deliver(answer: str, error: Exception | None) -> None:
            if future.done():
                return
            if error is not None:
                future.set_exception(error)
            else:
                future.set_result(answer)

        def read() -> None:
            answer = ""
            error = None
            try:
                reader = self._reader if self._reader is not None else self._read_terminal
                answer = reader(f"[{request.task_id}] {request.question}\n> ")
                if not isinstance(answer, str):
                    raise TypeError("terminal reader must return text")
            except EOFError:
                pass
            except Exception as exc:
                error = RuntimeError("Terminal input failed")
                error.__cause__ = exc
            finally:
                self._read_lock.release()
            try:
                loop.call_soon_threadsafe(deliver, answer, error)
            except RuntimeError:
                # The application may have closed the loop after a timeout.
                pass

        worker = threading.Thread(target=read, name="agentverse-terminal-input", daemon=True)
        try:
            worker.start()
        except Exception:
            self._read_lock.release()
            raise
        answer = (await future).strip()
        return self._decision(request, bool(answer), answer or "No clarification answer supplied")

    @staticmethod
    def _decision(
        request: HITLRequest,
        approved: bool,
        answer: str,
        *,
        source: Literal["human", "policy"] = "human",
    ) -> HITLDecision:
        return HITLDecision(
            request_id=request.request_id, approved=approved, answer=answer, source=source
        )
