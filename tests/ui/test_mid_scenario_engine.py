"""Exercise real discussion turns and file tools without a model or search service."""

import json
from collections import deque
from pathlib import Path

import pytest

from bus.messages import MessageBus
from protocol.models import ModelResponse, ToolCall, ToolResult
from ui import scenario
from ui.scenario_engine import discussion_engine

NEWSROOM_TOOLS = {
    "chief_editor": ["file_read", "file_write"],
    "reporter": ["web_search", "file_write"],
    "fact_checker": ["web_search", "file_read", "file_write"],
    "editor": ["file_read", "file_write"],
}
LEAD = "Source note for this offline rehearsal: newsroom-47."


def tool_call(name, **arguments):
    call = ToolCall(call_id=name, name=name, arguments=arguments)
    return ModelResponse(tool_calls=[call], tokens=50)


def write_and_report(path, text):
    return (
        tool_call("file_write", path=path, text=text),
        ModelResponse(content=f"My draft is in {path}.", tokens=50),
    )


class NewsroomModel:
    def __init__(self, *, prior_path=None):
        self.requests = []
        self.responses = deque()
        if prior_path:
            self.responses.append(tool_call("file_read", path=prior_path))
        self.responses.extend(write_and_report("outputs/desk.md", "Desk notes"))
        self.responses.extend(write_and_report("outputs/lead.md", LEAD))
        self.responses.append(tool_call("file_read", path="inputs/shared/reporter/lead.md"))
        self.responses.extend(write_and_report("outputs/checks.md", "Checked the source note"))
        self.responses.extend(write_and_report("outputs/article.md", "Article draft"))

    async def complete(self, messages, tools, *, max_tokens):
        self.requests.append((messages, {tool.name for tool in tools}))
        return self.responses.popleft()

    def reads(self):
        results = {}
        for messages, _ in self.requests:
            for message in messages:
                if message.role == "tool" and message.tool_call_id == "file_read":
                    result = ToolResult.model_validate_json(message.content)
                    assert result.ok, result.error
                    results[result.data["path"]] = result.data["text"]
        return results


def prepare_newsroom(tmp_path):
    packet = tmp_path / "scenario_2_newsroom"
    packet.mkdir()
    roles = "".join(
        f"## {role} ({role})\n```text\nYou are {role}.\n```\n" for role in NEWSROOM_TOOLS
    )
    (packet / "01_role_initialization.md").write_text("# Newsroom\n" + roles, encoding="utf-8")
    for name, goal in (("02_task_1.md", "Prepare a report."), ("03_task_2.md", "Check a claim.")):
        (packet / name).write_text(f"```text\n{goal}\n```\n", encoding="utf-8")
    profiles = Path(__file__).resolve().parents[2] / "profiles/scenarios"
    if not (profiles / "s2_newsroom.yaml").is_file():
        # The D and A branches can be tested before C's policy branch is included.
        profiles = tmp_path / "profiles"
        profiles.mkdir()
        (profiles / "s2_newsroom.yaml").write_text(
            json.dumps({"scenario_id": "scenario_2_newsroom", "roles": NEWSROOM_TOOLS}),
            encoding="utf-8",
        )
    session = tmp_path / "session"
    scenario.init_session(packet, session)
    return session, profiles


async def test_newsroom_candidate_sharing_and_accepted_handoff(tmp_path):
    pytest.importorskip("orchestrator.discussion")
    session, profiles = prepare_newsroom(tmp_path)
    first = NewsroomModel()
    engine = discussion_engine(first, max_rounds=1, max_output_tokens=512)
    assert await scenario.run_task(session, 1, profiles, engine) is None
    assert not first.responses
    assert first.reads() == {"inputs/shared/reporter/lead.md": LEAD}
    for role, allowed in NEWSROOM_TOOLS.items():
        first_offer = next(
            names for messages, names in first.requests if f"You are {role}." in messages[0].content
        )
        assert first_offer == set(allowed)

    task = session / "tasks/task-1"
    (attempt,) = (task / "attempts").iterdir()
    candidate = "outputs/reporter/lead.md"
    assert (attempt / candidate).read_text(encoding="utf-8") == LEAD
    assert not (task / "accepted").exists()
    with pytest.raises(ValueError, match="accept Task 1"):
        await scenario.run_task(session, 2, profiles, engine)
    scenario.accept_attempt(session, 1, attempt.name, (candidate,))

    prior_path = "inputs/reporter/lead.md"
    second = NewsroomModel(prior_path=prior_path)
    engine = discussion_engine(second, max_rounds=1, max_output_tokens=512)
    assert await scenario.run_task(session, 2, profiles, engine) is None
    assert not second.responses
    assert second.reads() == {prior_path: LEAD, "inputs/shared/reporter/lead.md": LEAD}
    assert any(prior_path in message.content for message in second.requests[0][0])
    (attempt,) = (session / "tasks/task-2/attempts").iterdir()
    for agent in NEWSROOM_TOOLS:
        inputs = attempt / "workspaces" / agent / "inputs"
        assert (inputs / "reporter/lead.md").read_text(encoding="utf-8") == LEAD
        assert not (inputs / "editor/article.md").exists()
    summary = json.loads((attempt / "discussion-summary.json").read_text(encoding="utf-8"))
    assert summary["stop_reason"] == "max_rounds" and summary["turns"] == 4
    assert summary["prior_task_reads"] == [{"speaker": "chief_editor", "path": prior_path}]


async def test_newsroom_receives_instructor_message_between_real_rounds(tmp_path):
    pytest.importorskip("orchestrator.discussion")
    session, profiles = prepare_newsroom(tmp_path)
    model = NewsroomModel()
    second_round = ("reporter", "fact_checker", "editor", "chief_editor")
    for speaker in second_round:
        model.responses.extend(write_and_report(f"outputs/{speaker}-revision.md", "Revised note"))
    engine = discussion_engine(model, max_rounds=2, max_output_tokens=512)
    correction = "请在修订中保留证据的不确定性。"
    public_turns = []
    first_round_calls = []

    async def post_after_first_round(request, human, emit):
        def publish(speaker, content):
            emit(speaker, content)
            public_turns.append((speaker, content))
            if len(public_turns) == 4:
                first_round_calls.append(len(model.requests))
                assert scenario.main(["say", str(session), "--text", correction]) == 0

        return await engine(request, human, publish)

    assert await scenario.run_task(session, 1, profiles, post_after_first_round) is None
    assert not model.responses
    (boundary,) = first_round_calls
    saw_correction = [
        any(correction in message.content for message in messages) for messages, _ in model.requests
    ]
    assert not any(saw_correction[:boundary]) and all(saw_correction[boundary:])
    assert [speaker for speaker, _ in public_turns] == [*NEWSROOM_TOOLS, *second_round]
    group = json.loads((session / "scenario.json").read_text(encoding="utf-8"))["conversation_id"]
    bus = MessageBus(session / "messages.sqlite3")
    messages = [(event.sender, event.text) for event in bus.history(group, "reporter")]
    assert messages == [
        ("instructor", "Prepare a report."),
        *public_turns[:4],
        ("instructor", correction),
        *public_turns[4:],
    ]
    assert all(LEAD not in text for _, text in messages)
