"""Exercise private chat through the CLI without making model API calls."""

import pytest

from bus.messages import MessageBus
from protocol.models import ModelResponse
from runtime.client import ModelCallError
from ui import cli, private_chat

WRITER_PROMPT = "Write a concise reply as the assigned writer."


class FakeDeepSeekClient:
    def __init__(self):
        self.requests = []
        self.reply = "建议先整理报告大纲。"
        self.error = None
        self.on_complete = None
        self.closed = False

    async def complete(self, messages, tools, *, max_tokens):
        self.requests.append((messages, tools, max_tokens))
        if self.on_complete is not None:
            self.on_complete()
        if self.error is not None:
            raise self.error
        return ModelResponse(content=self.reply)

    async def aclose(self):
        self.closed = True


@pytest.fixture
def client(monkeypatch):
    fake = FakeDeepSeekClient()
    monkeypatch.setattr(
        private_chat.DeepSeekClient, "from_environment", classmethod(lambda cls: fake)
    )
    return fake


@pytest.fixture
def profiles(tmp_path):
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "writer.yaml").write_text(
        f"agent_id: writer\nrole: worker\nsystem_prompt: {WRITER_PROMPT}\n"
        "tools: [file_read, file_write]\n",
        encoding="utf-8",
    )
    (profiles / "reviewer.yaml").write_text(
        "agent_id: reviewer\nrole: worker\nsystem_prompt: Review the report.\n",
        encoding="utf-8",
    )
    return profiles


@pytest.fixture
def bus(tmp_path):
    bus = MessageBus(tmp_path / "messages.sqlite3")
    bus.create_conversation("direct", "private", ["user", "writer"])
    return bus


def chat(bus, profiles, text, *, conversation="direct", sender="user", agent="writer"):
    return cli.main(
        [
            "messages",
            "--db",
            str(bus.database),
            "chat",
            conversation,
            "--sender",
            sender,
            "--agent",
            agent,
            "--text",
            text,
            "--profiles-dir",
            str(profiles),
        ]
    )


def test_private_chat_uses_only_its_text_history_and_persists_reply(bus, profiles, client, capsys):
    bus.create_conversation("separate", "private", ["user", "writer"])
    bus.publish("separate", "user", "message", "Do not include this other conversation.")
    bus.publish("direct", "user", "message", "我想写一份报告。")
    bus.publish("direct", "writer", "message", "你想从哪里开始？")
    bus.publish("direct", "writer", "handoff", "This event is not a text chat message.")
    question = "下一步怎么做？"

    assert chat(bus, profiles, question) == 0
    output = capsys.readouterr()
    assert not output.err
    assert client.reply in output.out

    ((messages, tools, max_tokens),) = client.requests
    assert messages[0].role == "system"
    assert WRITER_PROMPT in messages[0].content
    assert [(entry.role, entry.content) for entry in messages[1:]] == [
        ("user", "我想写一份报告。"),
        ("assistant", "你想从哪里开始？"),
        ("user", question),
    ]
    assert tools == []
    assert max_tokens > 0
    assert client.closed

    history = MessageBus(bus.database).history("direct", "user")
    assert [(event.sequence, event.sender, event.kind, event.text) for event in history[-2:]] == [
        (4, "user", "message", question),
        (5, "writer", "message", client.reply),
    ]
    assert (
        cli.main(["messages", "--db", str(bus.database), "history", "direct", "--as-id", "user"])
        == 0
    )
    output = capsys.readouterr()
    assert question in output.out and client.reply in output.out
    assert "Do not include" not in output.out


@pytest.mark.parametrize(
    ("conversation", "sender", "agent"),
    [("group", "user", "writer"), ("direct", "outsider", "writer"), ("direct", "user", "reviewer")],
)
def test_wrong_conversation_or_member_is_rejected_before_model_call(
    bus, profiles, client, conversation, sender, agent
):
    bus.create_conversation("group", "group", ["user", "writer", "reviewer"])

    assert chat(bus, profiles, "Hello", conversation=conversation, sender=sender, agent=agent) == 2
    assert client.requests == []
    assert bus.history(conversation, "user") == []


@pytest.mark.parametrize("failure", ["provider", "empty_reply"])
def test_failed_reply_does_not_leave_half_an_exchange(bus, profiles, client, failure):
    previous = bus.publish("direct", "user", "message", "Previous question")
    if failure == "provider":
        client.error = ModelCallError("Provider unavailable")
    else:
        client.reply = " \n"

    assert chat(bus, profiles, "New question") == 2
    assert client.closed
    assert bus.history("direct", "user") == [previous]


def test_private_chat_does_not_store_reply_based_on_a_stale_history(bus, profiles, client):
    client.on_complete = lambda: bus.publish("direct", "user", "message", "Concurrent message")

    assert chat(bus, profiles, "New question") == 2
    assert [
        (event.sequence, event.sender, event.text) for event in bus.history("direct", "user")
    ] == [(1, "user", "Concurrent message")]
