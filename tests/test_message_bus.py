"""Exercise the persistent membership and delivery boundaries."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest

from bus.messages import ConversationExistsError, MessageBus, MessageBusError


def test_conversation_reopens_with_ordered_unicode_history_and_cursor(tmp_path):
    database = tmp_path / "messages.db"
    bus = MessageBus(database)
    bus.create_conversation("资料组", "group", ["主持", "写手", "审稿"])
    events = [
        bus.publish("资料组", "主持", "message", "开始写项目说明。"),
        bus.publish("资料组", "主持", "assignment", "请整理来源。"),
        bus.publish("资料组", "写手", "delegation", "请求审稿。"),
        bus.publish("资料组", "审稿", "negotiation", "先完成引用核对。"),
        bus.publish("资料组", "写手", "handoff", "已准备摘要。\n请复核。"),
    ]

    reopened = MessageBus(database)
    assert reopened.history("资料组", "审稿") == events
    assert reopened.history("资料组", "写手", after=2) == events[2:]
    assert reopened.history("资料组", "主持", after=5) == []
    assert [event.sequence for event in events] == [1, 2, 3, 4, 5]
    assert all(datetime.fromisoformat(event.timestamp).tzinfo == UTC for event in events)
    assert reopened.publish("资料组", "主持", "message", "继续。 ").sequence == 6


def test_private_and_group_histories_are_scoped_to_members(tmp_path):
    bus = MessageBus(tmp_path / "messages.db")
    bus.create_conversation("direct", "private", ["alice", "bob"])
    bus.create_conversation("team", "group", ["alice", "bob", "carol"])
    private = bus.publish("direct", "alice", "message", "private report")
    shared = bus.publish("team", "carol", "message", "shared report")

    assert private.sequence == shared.sequence == 1
    assert bus.history("direct", "bob") == [private]
    assert bus.history("team", "alice") == [shared]
    with pytest.raises(PermissionError):
        bus.history("direct", "carol")
    with pytest.raises(PermissionError):
        bus.publish("direct", "carol", "message", "outside this conversation")
    with pytest.raises(PermissionError):
        bus.history("team", "outsider")
    with pytest.raises(PermissionError):
        bus.publish("team", "outsider", "handoff", "outside this conversation")
    with pytest.raises(PermissionError):
        bus.history("missing", "alice")

    assert bus.history("direct", "bob") == [private]
    assert bus.history("team", "carol") == [shared]


@pytest.mark.parametrize(
    ("kind", "members", "error"),
    [
        ("private", ["a"], "exactly two"),
        ("private", ["a", "b", "c"], "exactly two"),
        ("group", ["a"], "at least two"),
        ("group", ["a", " a "], "unique"),
        ("group", "ab", "separate identities"),
        ("other", ["a", "b"], "kind"),
    ],
)
def test_invalid_conversation_never_reserves_id(tmp_path, kind, members, error):
    bus = MessageBus(tmp_path / "messages.db")
    with pytest.raises(MessageBusError, match=error):
        bus.create_conversation("reusable", kind, members)
    bus.create_conversation("reusable", "private", ["a", "b"])
    assert bus.history("reusable", "b") == []


def test_duplicate_conversation_preserves_existing_members_and_events(tmp_path):
    bus = MessageBus(tmp_path / "messages.db")
    bus.create_conversation("team", "private", ["a", "b"])
    original = bus.publish("team", "a", "message", "unchanged")
    with pytest.raises(ConversationExistsError):
        bus.create_conversation("team", "group", ["c", "d"])
    with pytest.raises(PermissionError):
        bus.history("team", "c")
    assert bus.history("team", "b") == [original]


def test_invalid_events_and_cursor_are_rejected(tmp_path):
    bus = MessageBus(tmp_path / "messages.db")
    bus.create_conversation("team", "private", ["a", "b"])
    with pytest.raises(MessageBusError, match="Event kind"):
        bus.publish("team", "a", "tool_result", "raw tool result")
    with pytest.raises(MessageBusError, match="Event text"):
        bus.publish("team", "a", "message", " ")
    for cursor in (-1, True, 1.5):
        with pytest.raises(MessageBusError, match="cursor"):
            bus.history("team", "b", after=cursor)
    assert bus.publish("team", "a", "message", "first valid message").sequence == 1


def test_concurrent_publish_allocates_unique_sequences(tmp_path):
    bus = MessageBus(tmp_path / "messages.db")
    bus.create_conversation("team", "group", ["a", "b"])

    def publish(index):
        return bus.publish("team", "a", "message", str(index))

    with ThreadPoolExecutor(max_workers=4) as pool:
        sent = list(pool.map(publish, range(12)))

    history = bus.history("team", "b")
    assert history == sorted(sent, key=lambda event: event.sequence)
    assert [event.sequence for event in history] == list(range(1, 13))
