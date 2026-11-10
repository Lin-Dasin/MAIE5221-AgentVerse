import asyncio
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from orchestrator.approvals import InteractiveApprover
from protocol.models import HITLRequest
from ui.clarification import TerminalClarification


def request(kind="clarification", request_id="question-1"):
    return HITLRequest(
        request_id=request_id,
        task_id="report",
        kind=kind,
        action="choose_language",
        question="Which language should the report use?",
    )


async def wait_for_thread_state(predicate):
    async with asyncio.timeout(1):
        while not predicate():
            await asyncio.sleep(0.001)


async def test_nonempty_answer_keeps_request_identity_and_human_source():
    prompts = []

    def reader(prompt):
        prompts.append(prompt)
        return "  中文\n"

    inquiry = request()
    result = await TerminalClarification(reader)(inquiry)
    assert result.request_id == inquiry.request_id
    assert result.source == "human" and result.approved
    assert result.answer == "中文"
    assert len(prompts) == 1 and inquiry.question in prompts[0]


@pytest.mark.parametrize("answer", ["", " \t\n"])
async def test_empty_answer_is_a_clean_denial(answer):
    result = await TerminalClarification(lambda prompt: answer)(request())
    assert not result.approved
    assert result.request_id == "question-1" and result.source == "human"


async def test_eof_is_a_clean_denial():
    def eof(prompt):
        raise EOFError

    result = await TerminalClarification(eof)(request())
    assert not result.approved
    assert result.request_id == "question-1" and result.source == "human"


@pytest.mark.parametrize("kind", ["permission", "correction"])
async def test_other_request_kinds_are_declined_without_reading(kind):
    def unexpected_prompt(prompt):
        pytest.fail("non-clarification requests must not prompt")

    result = await TerminalClarification(unexpected_prompt)(request(kind))
    assert not result.approved and result.source == "policy"


async def test_unexpected_read_error_surfaces_and_releases_reader():
    calls = 0

    def reader(prompt):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("synthetic input device failure")
        return "English"

    callback = TerminalClarification(reader)
    with pytest.raises(RuntimeError, match="Terminal input failed") as error:
        await callback(request())
    assert isinstance(error.value.__cause__, OSError)
    assert (await callback(request(request_id="question-2"))).approved


@pytest.mark.parametrize("termination", ["timeout", "cancel"])
async def test_pending_reader_is_not_duplicated_and_late_answer_is_discarded(termination):
    started, release = threading.Event(), threading.Event()
    threads = []

    def reader(prompt):
        threads.append(threading.current_thread())
        if len(threads) == 1:
            started.set()
            release.wait()
            return "late answer for the first request"
        return "fresh answer"

    callback = TerminalClarification(reader)
    operation = (
        InteractiveApprover(callback, timeout_sec=0.05).decide(request())
        if termination == "timeout"
        else callback(request())
    )
    pending = asyncio.create_task(operation)
    try:
        await wait_for_thread_state(started.is_set)
        assert threads[0].daemon
        if termination == "timeout":
            result = await pending
            assert not result.approved and result.source == "timeout"
        else:
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        denied = await callback(request(request_id="question-2"))
        assert not denied.approved and denied.request_id == "question-2"
        assert len(threads) == 1
    finally:
        release.set()
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await wait_for_thread_state(lambda: all(not thread.is_alive() for thread in threads))
    fresh = await callback(request(request_id="question-3"))
    assert fresh.request_id == "question-3" and fresh.approved
    assert fresh.answer == "fresh answer"


def test_asyncio_run_can_finish_while_terminal_read_is_still_blocked():
    release, started, finished = threading.Event(), threading.Event(), threading.Event()
    readers, results, errors = [], [], []

    def reader(prompt):
        readers.append(threading.current_thread())
        started.set()
        release.wait()
        return "too late"

    def run_application():
        try:
            results.append(
                asyncio.run(
                    InteractiveApprover(TerminalClarification(reader), timeout_sec=0.05).decide(
                        request()
                    )
                )
            )
        except Exception as exc:
            errors.append(exc)
        finally:
            finished.set()

    application = threading.Thread(target=run_application, daemon=True)
    application.start()
    try:
        assert started.wait(timeout=1)
        assert finished.wait(timeout=1), "asyncio shutdown waited for blocked terminal input"
        assert not errors and len(results) == 1
        assert not results[0].approved and results[0].source == "timeout"
        assert readers[0].daemon and readers[0].is_alive()
    finally:
        release.set()
        application.join(timeout=1)
        for thread in readers:
            thread.join(timeout=1)


def test_real_stdin_pipe_timeout_does_not_hold_python_shutdown():
    code = """
import asyncio
from orchestrator.approvals import InteractiveApprover
from protocol.models import HITLRequest
from ui.clarification import TerminalClarification
request = HITLRequest(
    task_id="report", kind="clarification", action="language", question="Language?"
)
decision = asyncio.run(
    InteractiveApprover(TerminalClarification(), timeout_sec=0.05).decide(request)
)
print("\\n" + decision.model_dump_json(), flush=True)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[2],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        # Keep stdin open and send no EOF. communicate() would close it, masking
        # the real bug where the unanswered prompt prevents interpreter shutdown.
        try:
            returncode = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pytest.fail("unanswered real stdin pipe prevented Python shutdown")
        output, error = process.stdout.read(), process.stderr.read()
        assert returncode == 0, error
        decision = json.loads(output.splitlines()[-1])
        assert decision["source"] == "timeout" and not decision["approved"]
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()


def test_real_stdin_preserves_buffered_answers_for_sequential_requests():
    code = """
import asyncio
import json
from protocol.models import HITLRequest
from ui.clarification import TerminalClarification

async def run():
    terminal = TerminalClarification()
    answers = []
    for task_id in ("first", "second"):
        request = HITLRequest(
            task_id=task_id, kind="clarification", action="title", question="Title?"
        )
        answers.append((await terminal(request)).answer)
    print(json.dumps(answers))

asyncio.run(run())
"""
    process = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[2],
        input="first title\nsecond title\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=5,
        check=True,
    )
    assert json.loads(process.stdout.split("> ")[-1]) == ["first title", "second title"]
