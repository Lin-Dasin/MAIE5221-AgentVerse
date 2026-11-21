"""Verify that a group reply resumes the original request across CLI processes."""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from bus.messages import MessageBus
from orchestrator.approvals import InteractiveApprover
from protocol.models import HITLRequest
from ui import cli
from ui.group_clarification import GroupClarification
from ui.group_run import GroupTrace
from ui.h1_demo import run_demo

TITLE = "项目进度"


@pytest.fixture
def bus(tmp_path):
    bus = MessageBus(tmp_path / "conversations.sqlite3")
    participants = ["user", "writer", "orchestrator", "reader"]
    for conversation in ("room", "other"):
        bus.create_conversation(conversation, "group", participants)
    return bus


def callback(bus, timeout_sec=10):
    return GroupClarification(
        bus, "room", "user", "orchestrator", "run-one", timeout_sec=timeout_sec
    )


def request(kind="clarification"):
    return HITLRequest(
        request_id="question-one",
        task_id="report",
        kind=kind,
        action="report_title",
        question="报告标题用什么？",
    )


def reply_command(bus, request_id, *, conversation="room", sender="user", text=TITLE):
    return [
        "messages",
        "--db",
        str(bus.database),
        "reply",
        conversation,
        "--sender",
        sender,
        "--request-id",
        request_id,
        "--text",
        text,
    ]


async def pending_request(bus):
    async with asyncio.timeout(5):
        while True:
            for event in bus.history("room", "reader"):
                payload = json.loads(event.text)
                if payload["event"] == "clarification_requested":
                    return event, payload
            await asyncio.sleep(0.01)


async def test_group_reply_resumes_task_and_uses_answer_in_the_original_report(bus, tmp_path):
    running = asyncio.create_task(
        run_demo(tmp_path / "runs", ask=callback(bus), approval_timeout_sec=10)
    )
    try:
        event, pending = await pending_request(bus)
        request_id = pending["request_id"]
        assert event.sender == "orchestrator"
        assert pending["run_id"] == "run-one"
        assert pending["task_id"] == "write-clarified-report"
        assert pending["question"] == "这份报告要用什么标题？"
        assert not running.done()

        for conversation, sender in [("other", "user"), ("room", "reader"), ("room", "outsider")]:
            assert (
                cli.main(reply_command(bus, request_id, conversation=conversation, sender=sender))
                == 2
            )
        assert cli.main(reply_command(bus, request_id, text=" \t")) == 2
        assert len(bus.history("room", "reader")) == 1
        assert bus.history("other", "user") == []

        process = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "ui.cli", *reply_command(bus, request_id)],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
        )
        assert process.returncode == 0, process.stderr
        assert cli.main(reply_command(bus, request_id)) == 2
        report = await asyncio.wait_for(running, timeout=5)
    finally:
        if not running.done():
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)

    assert report.status == "success"
    assert report.subtasks_passed == report.subtasks_total == 1
    (decision,) = report.raw_result.tasks[0].hitl_decisions
    assert decision.request_id == request_id and decision.source == "human"
    assert decision.approved and decision.answer == TITLE
    (artifact,) = report.artifacts
    assert (
        (tmp_path / "runs" / report.run_id / artifact)
        .read_text(encoding="utf-8")
        .startswith(f"# {TITLE}\n")
    )
    question, answer = MessageBus(bus.database).history("room", "reader")
    assert question == event
    assert answer.sender == "user"
    assert json.loads(answer.text) == {
        "event": "clarification_answered",
        "run_id": "run-one",
        "request_id": request_id,
        "answer": TITLE,
    }


async def test_approver_timeout_closes_pending_reply_before_storage_deadline(bus, tmp_path):
    question = request()
    decision = await asyncio.wait_for(
        InteractiveApprover(callback(bus, timeout_sec=10), timeout_sec=0.2).decide(question),
        timeout=3,
    )
    assert not decision.approved and decision.source == "timeout"
    assert decision.request_id == question.request_id
    trace = GroupTrace(tmp_path / "trace.jsonl", bus, "room", "orchestrator")
    await trace.hitl_decided("run-one", question, decision)
    before = bus.history("room", "reader")
    payloads = [json.loads(event.text) for event in before]
    assert [payload["event"] for payload in payloads] == [
        "clarification_requested",
        "clarification_timed_out",
    ]
    assert all(payload["request_id"] == question.request_id for payload in payloads)
    assert cli.main(reply_command(bus, question.request_id)) == 2
    assert bus.history("room", "reader") == before


@pytest.mark.parametrize("kind", ["permission", "correction"])
async def test_group_text_reply_does_not_resolve_other_human_requests(bus, kind):
    question = request(kind)
    result = await callback(bus)(question)
    assert not result.approved and result.source == "policy"
    assert result.request_id == question.request_id
    assert bus.history("room", "reader") == []
