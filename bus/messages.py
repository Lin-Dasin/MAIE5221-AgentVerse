"""Persistent private and group conversations for local callers."""

import json
import math
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
CREATE TABLE IF NOT EXISTS clarification_requests (
    conversation_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    requester TEXT NOT NULL,
    expires_at REAL NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'answered', 'closed')),
    answer TEXT,
    PRIMARY KEY (conversation_id, request_id),
    FOREIGN KEY (conversation_id, requester)
        REFERENCES conversation_members(conversation_id, member),
    CHECK ((state = 'answered') = (answer IS NOT NULL))
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

    def _require_distinct_members(
        self, connection: sqlite3.Connection, conversation_id: str, sender: str, agent_id: str
    ) -> str:
        if sender == agent_id:
            raise MessageBusError("The sender and recipient must be distinct members")
        self._require_member(connection, conversation_id, sender)
        self._require_member(connection, conversation_id, agent_id)
        (kind,) = connection.execute(
            "SELECT kind FROM conversations WHERE conversation_id = ?", (conversation_id,)
        ).fetchone()
        return kind

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

    def chat_history(
        self, conversation_id: str, sender: str, agent_id: str
    ) -> tuple[str, list[MessageEvent]]:
        """Read the conversation kind and history after validating both participants."""
        conversation_id = _identifier(conversation_id, "Conversation ID")
        sender = _identifier(sender, "Sender")
        agent_id = _identifier(agent_id, "Agent ID")
        with self._connection() as connection:
            connection.execute("BEGIN")
            kind = self._require_distinct_members(connection, conversation_id, sender, agent_id)
            return kind, self._history(connection, conversation_id, after=0)

    def group_members(self, conversation_id: str, sender: str, notifier: str) -> frozenset[str]:
        """Check the identities for a group run and return its eligible member IDs."""
        conversation_id = _identifier(conversation_id, "Conversation ID")
        sender = _identifier(sender, "Sender")
        notifier = _identifier(notifier, "Notifier")
        with self._connection() as connection:
            connection.execute("BEGIN")
            kind = self._require_distinct_members(connection, conversation_id, sender, notifier)
            if kind != "group":
                raise MessageBusError("Starting a task requires a group conversation")
            rows = connection.execute(
                "SELECT member FROM conversation_members WHERE conversation_id = ?",
                (conversation_id,),
            )
            return frozenset(member for (member,) in rows)

    def request_clarification(
        self,
        conversation_id: str,
        notifier: str,
        requester: str,
        *,
        run_id: str,
        request_id: str,
        task_id: str,
        question: str,
        timeout_sec: float,
    ) -> MessageEvent:
        """Open a bounded request and show the question in the same group."""
        conversation_id = _identifier(conversation_id, "Conversation ID")
        notifier = _identifier(notifier, "Notifier")
        requester = _identifier(requester, "Requester")
        run_id = _identifier(run_id, "Run ID")
        request_id = _identifier(request_id, "Request ID")
        task_id = _identifier(task_id, "Task ID")
        _require_text(question)
        if (
            type(timeout_sec) not in {int, float}
            or timeout_sec > timedelta.max.total_seconds()
            or not math.isfinite(timeout_sec)
            or timeout_sec <= 0
        ):
            raise MessageBusError("Clarification timeout must be finite and positive")

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            kind = self._require_distinct_members(connection, conversation_id, notifier, requester)
            if kind != "group":
                raise MessageBusError("Clarification requires a group conversation")
            try:
                expires_at = datetime.now(UTC) + timedelta(seconds=timeout_sec)
            except OverflowError:
                raise MessageBusError("Clarification timeout is out of range") from None
            try:
                connection.execute(
                    "INSERT INTO clarification_requests "
                    "(conversation_id, request_id, run_id, requester, expires_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (conversation_id, request_id, run_id, requester, expires_at.timestamp()),
                )
            except sqlite3.IntegrityError:
                raise MessageBusError("Clarification request already exists") from None
            text = json.dumps(
                {
                    "event": "clarification_requested",
                    "run_id": run_id,
                    "request_id": request_id,
                    "task_id": task_id,
                    "question": question,
                    "expires_at": expires_at.isoformat(),
                },
                ensure_ascii=False,
            )
            sequence = self._current_sequence(connection, conversation_id) + 1
            return self._insert_event(
                connection, conversation_id, sequence, notifier, "message", text
            )

    def reply_to_clarification(
        self, conversation_id: str, sender: str, request_id: str, answer: str
    ) -> MessageEvent:
        """Record the authorized answer once and publish it to the group."""
        conversation_id = _identifier(conversation_id, "Conversation ID")
        sender = _identifier(sender, "Sender")
        request_id = _identifier(request_id, "Request ID")
        _require_text(answer)
        answer = answer.strip()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_member(connection, conversation_id, sender)
            row = connection.execute(
                "SELECT run_id, requester, state, expires_at FROM clarification_requests "
                "WHERE conversation_id = ? AND request_id = ?",
                (conversation_id, request_id),
            ).fetchone()
            if row is None:
                raise MessageBusError("No clarification request with that ID in this conversation")
            run_id, requester, state, expires_at = row
            if requester != sender:
                raise PermissionError("Only the task requester can answer this clarification")
            if state != "pending":
                raise MessageBusError(f"Clarification request is already {state}")
            if datetime.now(UTC).timestamp() >= expires_at:
                raise MessageBusError("Clarification request has expired")
            connection.execute(
                "UPDATE clarification_requests SET state = 'answered', answer = ? "
                "WHERE conversation_id = ? AND request_id = ?",
                (answer, conversation_id, request_id),
            )
            text = json.dumps(
                {
                    "event": "clarification_answered",
                    "run_id": run_id,
                    "request_id": request_id,
                    "answer": answer,
                },
                ensure_ascii=False,
            )
            sequence = self._current_sequence(connection, conversation_id) + 1
            return self._insert_event(
                connection, conversation_id, sequence, sender, "message", text
            )

    def get_clarification_answer(
        self, conversation_id: str, run_id: str, request_id: str
    ) -> str | None:
        """Read a saved answer scoped to the waiting run."""
        conversation_id = _identifier(conversation_id, "Conversation ID")
        run_id = _identifier(run_id, "Run ID")
        request_id = _identifier(request_id, "Request ID")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT answer FROM clarification_requests "
                "WHERE conversation_id = ? AND run_id = ? AND request_id = ? "
                "AND state = 'answered'",
                (conversation_id, run_id, request_id),
            ).fetchone()
        return row[0] if row else None

    def close_clarification(self, conversation_id: str, run_id: str, request_id: str) -> None:
        """Close a request that is still waiting when its run stops polling."""
        conversation_id = _identifier(conversation_id, "Conversation ID")
        run_id = _identifier(run_id, "Run ID")
        request_id = _identifier(request_id, "Request ID")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE clarification_requests SET state = 'closed' "
                "WHERE conversation_id = ? AND run_id = ? AND request_id = ? AND state = 'pending'",
                (conversation_id, run_id, request_id),
            )

    def record_chat_exchange(
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
            self._require_distinct_members(connection, conversation_id, sender, agent_id)
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
