"""Persistent private and group conversations for local callers."""

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_EVENT_KINDS = ("message", "assignment", "delegation", "negotiation", "handoff")
_SCHEMA = """
BEGIN;
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT NOT NULL PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('private', 'group'))
);
CREATE TABLE IF NOT EXISTS conversation_members (
    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
    member TEXT NOT NULL,
    PRIMARY KEY (conversation_id, member)
);
CREATE TABLE IF NOT EXISTS message_events (
    conversation_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    sender TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (
        kind IN ('message', 'assignment', 'delegation', 'negotiation', 'handoff')
    ),
    text TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    PRIMARY KEY (conversation_id, sequence),
    FOREIGN KEY (conversation_id, sender) REFERENCES conversation_members(conversation_id, member)
);
COMMIT;
"""


class MessageBusError(ValueError):
    """An invalid request or database operation."""


class ConversationExistsError(MessageBusError):
    """The conversation ID is already registered."""


@dataclass(frozen=True)
class MessageEvent:
    conversation_id: str
    sequence: int
    sender: str
    kind: str
    text: str
    timestamp: str


def _identifier(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MessageBusError(f"{label} must be a non-empty string")
    return value.strip()


def _require_text(text: str) -> None:
    if not isinstance(text, str) or not text.strip():
        raise MessageBusError("Event text must be a non-empty string")


class MessageBus:
    """Store member-visible events. The caller supplies each local identity."""

    def __init__(self, database: Path) -> None:
        self.database = Path(database).resolve()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript(_SCHEMA)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        try:
            with closing(sqlite3.connect(self.database)) as connection, connection:
                connection.execute("PRAGMA foreign_keys = ON")
                yield connection
        except sqlite3.Error as exc:
            raise MessageBusError(f"Message database error: {exc}") from exc

    def create_conversation(self, conversation_id: str, kind: str, members: Iterable[str]) -> None:
        conversation_id = _identifier(conversation_id, "Conversation ID")
        if kind not in ("private", "group"):
            raise MessageBusError("Conversation kind must be private or group")
        if not isinstance(members, Iterable) or isinstance(members, (str, bytes)):
            raise MessageBusError("Members must be supplied as separate identities")
        participants = tuple(_identifier(member, "Member") for member in members)
        if len(set(participants)) != len(participants):
            raise MessageBusError("Conversation members must be unique")
        if kind == "private" and len(participants) != 2:
            raise MessageBusError("Private conversations require exactly two members")
        if kind == "group" and len(participants) < 2:
            raise MessageBusError("Group conversations require at least two members")

        with self._connection() as connection:
            try:
                connection.execute(
                    "INSERT INTO conversations (conversation_id, kind) VALUES (?, ?)",
                    (conversation_id, kind),
                )
            except sqlite3.IntegrityError:
                raise ConversationExistsError("Conversation ID already exists") from None
            connection.executemany(
                "INSERT INTO conversation_members (conversation_id, member) VALUES (?, ?)",
                ((conversation_id, member) for member in participants),
            )

    @staticmethod
    def _require_member(connection: sqlite3.Connection, conversation_id: str, member: str) -> None:
        row = connection.execute(
            "SELECT 1 FROM conversation_members WHERE conversation_id = ? AND member = ?",
            (conversation_id, member),
        ).fetchone()
        if row is None:
            raise PermissionError("Identity is not a member of this conversation")

    def _require_private_pair(
        self, connection: sqlite3.Connection, conversation_id: str, sender: str, agent_id: str
    ) -> None:
        if sender == agent_id:
            raise MessageBusError("Private chat requires distinct sender and agent identities")
        self._require_member(connection, conversation_id, sender)
        self._require_member(connection, conversation_id, agent_id)
        (kind,) = connection.execute(
            "SELECT kind FROM conversations WHERE conversation_id = ?", (conversation_id,)
        ).fetchone()
        if kind != "private":
            raise MessageBusError("Agent replies require a private conversation")

    @staticmethod
    def _current_sequence(connection: sqlite3.Connection, conversation_id: str) -> int:
        (sequence,) = connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) FROM message_events WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        return sequence

    @staticmethod
    def _insert_event(
        connection: sqlite3.Connection,
        conversation_id: str,
        sequence: int,
        sender: str,
        kind: str,
        text: str,
    ) -> MessageEvent:
        timestamp = datetime.now(UTC).isoformat()
        connection.execute(
            "INSERT INTO message_events "
            "(conversation_id, sequence, sender, kind, text, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (conversation_id, sequence, sender, kind, text, timestamp),
        )
        return MessageEvent(conversation_id, sequence, sender, kind, text, timestamp)

    def publish(self, conversation_id: str, sender: str, kind: str, text: str) -> MessageEvent:
        conversation_id = _identifier(conversation_id, "Conversation ID")
        sender = _identifier(sender, "Sender")
        if kind not in _EVENT_KINDS:
            raise MessageBusError(f"Event kind must be one of: {', '.join(_EVENT_KINDS)}")
        _require_text(text)

        with self._connection() as connection:
            # Serialize sequence allocation and insertion across database connections.
            connection.execute("BEGIN IMMEDIATE")
            self._require_member(connection, conversation_id, sender)
            sequence = self._current_sequence(connection, conversation_id) + 1
            return self._insert_event(connection, conversation_id, sequence, sender, kind, text)

    def private_chat_history(
        self, conversation_id: str, sender: str, agent_id: str
    ) -> list[MessageEvent]:
        """Read the current private exchange after validating both participants."""
        conversation_id = _identifier(conversation_id, "Conversation ID")
        sender = _identifier(sender, "Sender")
        agent_id = _identifier(agent_id, "Agent ID")
        with self._connection() as connection:
            connection.execute("BEGIN")
            self._require_private_pair(connection, conversation_id, sender, agent_id)
            return self._history(connection, conversation_id, after=0)

    def record_private_exchange(
        self,
        conversation_id: str,
        sender: str,
        agent_id: str,
        text: str,
        reply: str,
        *,
        expected_sequence: int,
    ) -> MessageEvent:
        """Append both messages only if the conversation still matches the read snapshot."""
        conversation_id = _identifier(conversation_id, "Conversation ID")
        sender = _identifier(sender, "Sender")
        agent_id = _identifier(agent_id, "Agent ID")
        _require_text(text)
        _require_text(reply)
        if type(expected_sequence) is not int or expected_sequence < 0:
            raise MessageBusError("Expected sequence must be a non-negative integer")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_private_pair(connection, conversation_id, sender, agent_id)
            if self._current_sequence(connection, conversation_id) != expected_sequence:
                raise MessageBusError(
                    "Conversation changed while waiting for the reply. Try again."
                )
            self._insert_event(
                connection, conversation_id, expected_sequence + 1, sender, "message", text
            )
            return self._insert_event(
                connection, conversation_id, expected_sequence + 2, agent_id, "message", reply
            )

    def history(
        self, conversation_id: str, requester: str, *, after: int = 0
    ) -> list[MessageEvent]:
        conversation_id = _identifier(conversation_id, "Conversation ID")
        requester = _identifier(requester, "Requester")
        if type(after) is not int or after < 0:
            raise MessageBusError("History cursor must be a non-negative integer")
        with self._connection() as connection:
            connection.execute("BEGIN")
            self._require_member(connection, conversation_id, requester)
            return self._history(connection, conversation_id, after=after)

    @staticmethod
    def _history(
        connection: sqlite3.Connection, conversation_id: str, *, after: int
    ) -> list[MessageEvent]:
        rows = connection.execute(
            "SELECT conversation_id, sequence, sender, kind, text, timestamp "
            "FROM message_events WHERE conversation_id = ? AND sequence > ? ORDER BY sequence",
            (conversation_id, min(after, 2**63 - 1)),
        )
        return [MessageEvent(*row) for row in rows]
