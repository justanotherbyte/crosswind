"""SQL storage for harnesses: sessions, their operations, and transcripts.

Rows are keyed by the harness's capability id, so several harnesses can share
one object. History and checkpoints are opaque bytes the subclass owns; the
transcript is `TranscriptMessage` JSON the base class writes.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Self

from typing_extensions import TypeIs

from ..core.sql import Sql
from ..core.timing import now_ms
from ..core.types import JSONValue
from .transcript import TranscriptMessage, message_from_wire, to_wire
from .types import (
    Approval,
    FileInput,
    HarnessInput,
    InputPart,
    OperationResult,
    OperationStatus,
    PendingOperation,
    SessionInfo,
    TextInput,
    ToolAnswer,
    ToolOutput,
    ToolRequest,
)

__all__ = ("HarnessStore", "OperationRow", "is_answers")

_SESSIONS = """CREATE TABLE IF NOT EXISTS cf_agents_harness_sessions (
  harness TEXT NOT NULL,
  id TEXT NOT NULL,
  parent TEXT,
  history BLOB,
  created_at INTEGER NOT NULL,
  PRIMARY KEY (harness, id)
) WITHOUT ROWID"""
_OPERATIONS = """CREATE TABLE IF NOT EXISTS cf_agents_harness_operations (
  harness TEXT NOT NULL,
  session TEXT NOT NULL,
  id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  input TEXT NOT NULL,
  message_id TEXT,
  participant TEXT,
  answers TEXT,
  status TEXT NOT NULL,
  checkpoint BLOB,
  attempts INTEGER NOT NULL DEFAULT 0,
  result TEXT,
  reason TEXT,
  created_at INTEGER NOT NULL,
  PRIMARY KEY (harness, session, id)
) WITHOUT ROWID"""
_MESSAGES = """CREATE TABLE IF NOT EXISTS cf_agents_harness_messages (
  harness TEXT NOT NULL,
  session TEXT NOT NULL,
  id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('system', 'user', 'assistant')),
  message TEXT NOT NULL,
  operation_id TEXT,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (harness, session, id)
) WITHOUT ROWID"""

# An operation whose input is a user message, not tool answers.
_IS_PROMPT = "json_extract(input, '$.answers') IS NULL"


def is_answers(input: HarnessInput) -> TypeIs[Sequence[ToolAnswer]]:
    """Whether ``input`` is tool answers rather than a user message."""
    return not isinstance(input, str) and isinstance(input[0], Approval | ToolOutput)


@dataclass(slots=True, frozen=True, kw_only=True)
class OperationRow:
    """One stored operation, parsed."""

    session: str
    id: str
    seq: int
    input: HarnessInput
    message_id: str | None
    participant: str | None
    answers: Sequence[ToolAnswer] | None
    status: OperationStatus
    checkpoint: bytes | None
    attempts: int
    text: str | None
    requests: Sequence[ToolRequest]
    reason: str | None

    @classmethod
    def from_sql(cls, row: dict[str, Any]) -> Self:
        """Parse a row of the operations table."""
        result = json.loads(row["result"]) if row["result"] is not None else {}
        answers = row["answers"]

        return cls(
            session=row["session"],
            id=row["id"],
            seq=row["seq"],
            input=_input_from_json(json.loads(row["input"])),
            message_id=row["message_id"],
            participant=row["participant"],
            answers=(
                None
                if answers is None
                else [_answer_from_json(answer) for answer in json.loads(answers)]
            ),
            status=row["status"],
            checkpoint=row["checkpoint"],
            attempts=row["attempts"],
            text=result.get("text"),
            requests=[ToolRequest(**r) for r in result.get("requests", ())],
            reason=row["reason"],
        )

    @property
    def is_open(self) -> bool:
        """Whether the operation is queued or running."""
        return self.status in ("queued", "running")

    def result(self) -> OperationResult:
        """Return the settled operation's result."""
        return OperationResult(
            session=self.session,
            operation_id=self.id,
            status=self.status,
            text=self.text,
            requests=self.requests,
            reason=self.reason,
        )

    def pending(self) -> PendingOperation:
        """Return the open operation's summary."""
        return PendingOperation(
            session=self.session,
            operation_id=self.id,
            status="running" if self.status == "running" else "queued",
        )


class HarnessStore:
    """Every SQL statement a harness runs; callers wrap multi-step writes."""

    __slots__ = ("harness", "sql")

    def __init__(self, sql: Sql, harness: str) -> None:
        self.sql = sql
        self.harness = harness

    def ensure_tables(self) -> None:
        """Create the tables (idempotent)."""
        self.sql(_SESSIONS)
        self.sql(_OPERATIONS)
        self.sql(_MESSAGES)

    # Sessions

    def ensure_session(self, session: str) -> None:
        """Create the session if it doesn't exist."""
        self.sql(
            "INSERT OR IGNORE INTO cf_agents_harness_sessions"
            " (harness, id, created_at) VALUES (?, ?, ?)",
            self.harness,
            session,
            now_ms(),
        )

    def has_session(self, session: str) -> bool:
        """Return whether the session exists."""
        return bool(
            self.sql(
                "SELECT id FROM cf_agents_harness_sessions"
                " WHERE harness = ? AND id = ?",
                self.harness,
                session,
            )
        )

    def sessions(self) -> list[SessionInfo]:
        """Return every session, oldest first."""
        rows = self.sql(
            "SELECT id, parent, EXISTS (SELECT 1 FROM cf_agents_harness_operations o"
            "   WHERE o.harness = s.harness AND o.session = s.id"
            "   AND o.status IN ('queued', 'running')) AS busy"
            " FROM cf_agents_harness_sessions s WHERE harness = ?"
            " ORDER BY created_at, id",
            self.harness,
        )
        return [
            SessionInfo(id=row["id"], parent=row["parent"], busy=bool(row["busy"]))
            for row in rows
        ]

    def fork_session(self, source: str, session: str) -> None:
        """Create ``session`` with a copy of ``source``'s history and transcript."""
        self.sql(
            "INSERT INTO cf_agents_harness_sessions"
            " (harness, id, parent, history, created_at)"
            " SELECT harness, ?, id, history, ? FROM cf_agents_harness_sessions"
            " WHERE harness = ? AND id = ?",
            session,
            now_ms(),
            self.harness,
            source,
        )
        self.sql(
            "INSERT INTO cf_agents_harness_messages"
            " (harness, session, id, seq, role, message, operation_id, updated_at)"
            " SELECT harness, ?, id, seq, role, message, operation_id, ?"
            " FROM cf_agents_harness_messages WHERE harness = ? AND session = ?",
            session,
            now_ms(),
            self.harness,
            source,
        )

    def history(self, session: str) -> bytes | None:
        """Return the session's history, or ``None`` if it has none."""
        rows = self.sql(
            "SELECT history FROM cf_agents_harness_sessions"
            " WHERE harness = ? AND id = ?",
            self.harness,
            session,
        )
        return rows[0]["history"] if rows else None

    def set_history(self, session: str, history: bytes | None) -> None:
        """Replace the session's history."""
        self.sql(
            "UPDATE cf_agents_harness_sessions SET history = ?"
            " WHERE harness = ? AND id = ?",
            history,
            self.harness,
            session,
        )

    # Transcript

    def messages(self, session: str) -> list[TranscriptMessage]:
        """Return the session's transcript, oldest first."""
        rows = self.sql(
            "SELECT message FROM cf_agents_harness_messages"
            " WHERE harness = ? AND session = ? ORDER BY seq",
            self.harness,
            session,
        )
        return [message_from_wire(json.loads(row["message"])) for row in rows]

    def message(self, session: str, id: str) -> TranscriptMessage | None:
        """Return one message, or ``None``."""
        rows = self.sql(
            "SELECT message FROM cf_agents_harness_messages"
            " WHERE harness = ? AND session = ? AND id = ?",
            self.harness,
            session,
            id,
        )
        return message_from_wire(json.loads(rows[0]["message"])) if rows else None

    def latest_assistant_message(self, session: str) -> TranscriptMessage | None:
        """Return the session's newest assistant message, or ``None``."""
        rows = self.sql(
            "SELECT message FROM cf_agents_harness_messages"
            " WHERE harness = ? AND session = ? AND role = 'assistant'"
            " ORDER BY seq DESC LIMIT 1",
            self.harness,
            session,
        )
        return message_from_wire(json.loads(rows[0]["message"])) if rows else None

    def put_message(
        self, session: str, message: TranscriptMessage, operation_id: str | None
    ) -> None:
        """Save a message in place of any with its id, else at the end."""
        self.sql(
            "INSERT INTO cf_agents_harness_messages"
            " (harness, session, id, seq, role, message, operation_id, updated_at)"
            " VALUES (?, ?, ?, (SELECT COALESCE(MAX(seq), 0) + 1"
            "   FROM cf_agents_harness_messages WHERE harness = ? AND session = ?),"
            " ?, ?, ?, ?)"
            " ON CONFLICT (harness, session, id) DO UPDATE SET"
            " role = excluded.role, message = excluded.message,"
            " operation_id = excluded.operation_id, updated_at = excluded.updated_at",
            self.harness,
            session,
            message.id,
            self.harness,
            session,
            message.role,
            json.dumps(to_wire(message)),
            operation_id,
            now_ms(),
        )

    def delete_messages(self, session: str) -> None:
        """Delete the session's transcript."""
        self.sql(
            "DELETE FROM cf_agents_harness_messages WHERE harness = ? AND session = ?",
            self.harness,
            session,
        )

    # Operations

    def insert(
        self,
        session: str,
        id: str,
        input: HarnessInput,
        *,
        message_id: str | None,
        participant: str | None,
    ) -> bool:
        """Queue an operation at the end of the session; ``False`` if ``id`` exists."""
        rows = self.sql(
            "INSERT INTO cf_agents_harness_operations"
            " (harness, session, id, seq, input, message_id, participant, status,"
            "  created_at)"
            " VALUES (?, ?, ?, (SELECT COALESCE(MAX(seq), 0) + 1"
            "   FROM cf_agents_harness_operations WHERE harness = ? AND session = ?),"
            " ?, ?, ?, 'queued', ?)"
            " ON CONFLICT DO NOTHING RETURNING id",
            self.harness,
            session,
            id,
            self.harness,
            session,
            json.dumps(_input_to_json(input)),
            message_id,
            participant,
            now_ms(),
        )
        return bool(rows)

    def get(self, session: str, id: str) -> OperationRow | None:
        """Return one operation, or ``None``."""
        rows = self.sql(
            "SELECT * FROM cf_agents_harness_operations"
            " WHERE harness = ? AND session = ? AND id = ?",
            self.harness,
            session,
            id,
        )
        return OperationRow.from_sql(rows[0]) if rows else None

    def open(self, session: str) -> list[OperationRow]:
        """Return the session's open operations, oldest first."""
        rows = self.sql(
            "SELECT * FROM cf_agents_harness_operations"
            " WHERE harness = ? AND session = ? AND status IN ('queued', 'running')"
            " ORDER BY seq",
            self.harness,
            session,
        )
        return [OperationRow.from_sql(row) for row in rows]

    def running(self) -> list[OperationRow]:
        """Return every running operation, in any session."""
        rows = self.sql(
            "SELECT * FROM cf_agents_harness_operations"
            " WHERE harness = ? AND status = 'running'",
            self.harness,
        )
        return [OperationRow.from_sql(row) for row in rows]

    def sessions_with_open_work(self) -> list[str]:
        """Return the sessions that have open operations."""
        rows = self.sql(
            "SELECT DISTINCT session FROM cf_agents_harness_operations"
            " WHERE harness = ? AND status IN ('queued', 'running')",
            self.harness,
        )
        return [row["session"] for row in rows]

    def start(self, session: str, id: str, *, attempts: int) -> None:
        """Mark the operation running with this many interruptions so far."""
        self.sql(
            "UPDATE cf_agents_harness_operations SET status = 'running', attempts = ?"
            " WHERE harness = ? AND session = ? AND id = ?",
            attempts,
            self.harness,
            session,
            id,
        )

    def save_checkpoint(self, session: str, id: str, checkpoint: bytes) -> bool:
        """Replace a running operation's checkpoint; ``False`` if it isn't running."""
        rows = self.sql(
            "UPDATE cf_agents_harness_operations SET checkpoint = ?"
            " WHERE harness = ? AND session = ? AND id = ? AND status = 'running'"
            " RETURNING id",
            checkpoint,
            self.harness,
            session,
            id,
        )
        return bool(rows)

    def settle(
        self,
        session: str,
        id: str,
        status: OperationStatus,
        *,
        text: str | None,
        requests: Sequence[ToolRequest],
        reason: str | None,
    ) -> bool:
        """Settle an open operation; ``False`` if it was already settled.

        An ``unanswered`` operation keeps its checkpoint, so ``retry`` resumes.
        """
        result: dict[str, JSONValue] = {
            "text": text,
            "requests": [_request_to_json(request) for request in requests],
        }

        rows = self.sql(
            "UPDATE cf_agents_harness_operations"
            " SET status = ?, result = ?, reason = ?,"
            "   checkpoint = CASE WHEN ? = 'unanswered' THEN checkpoint END"
            " WHERE harness = ? AND session = ? AND id = ?"
            " AND status IN ('queued', 'running') RETURNING id",
            status,
            json.dumps(result),
            reason,
            status,
            self.harness,
            session,
            id,
        )
        return bool(rows)

    def ran_after(self, session: str, id: str) -> bool:
        """Whether a later operation in the session has run or is running."""
        rows = self.sql(
            "SELECT 1 FROM cf_agents_harness_operations"
            " WHERE harness = ? AND session = ?"
            " AND status IN ('running', 'done', 'needs_input')"
            " AND seq > (SELECT seq FROM cf_agents_harness_operations"
            "   WHERE harness = ? AND session = ? AND id = ?) LIMIT 1",
            self.harness,
            session,
            self.harness,
            session,
            id,
        )
        return bool(rows)

    def reopen(self, session: str, id: str) -> None:
        """Queue an unanswered operation again, resuming from its checkpoint."""
        self.sql(
            "UPDATE cf_agents_harness_operations"
            " SET status = 'queued', attempts = 0, result = NULL, reason = NULL"
            " WHERE harness = ? AND session = ? AND id = ?",
            self.harness,
            session,
            id,
        )

    # Tool answers

    def asking(self, session: str, *, before: int) -> OperationRow | None:
        """Return the newest ``needs_input`` operation before ``before``.

        ``None`` if there is none, or a user message has run since it.
        """
        rows = self.sql(
            "SELECT * FROM cf_agents_harness_operations"
            " WHERE harness = ? AND session = ? AND status = 'needs_input'"
            " AND seq < ? ORDER BY seq DESC LIMIT 1",
            self.harness,
            session,
            before,
        )
        if not rows:
            return None

        asking = OperationRow.from_sql(rows[0])
        superseded = self.sql(
            "SELECT 1 FROM cf_agents_harness_operations"
            " WHERE harness = ? AND session = ? AND seq > ? AND seq < ?"
            f" AND status != 'queued' AND {_IS_PROMPT} LIMIT 1",
            self.harness,
            session,
            asking.seq,
            before,
        )
        return None if superseded else asking

    def answers_after(self, session: str, seq: int) -> list[ToolAnswer]:
        """Return the answers operations after ``seq`` recorded, in order."""
        rows = self.sql(
            "SELECT answers FROM cf_agents_harness_operations"
            " WHERE harness = ? AND session = ? AND seq > ? AND answers IS NOT NULL"
            " ORDER BY seq",
            self.harness,
            session,
            seq,
        )
        return [
            _answer_from_json(answer)
            for row in rows
            for answer in json.loads(row["answers"])
        ]

    def record_answers(
        self, session: str, id: str, answers: Sequence[ToolAnswer]
    ) -> None:
        """Record the answers an operation contributed."""
        self.sql(
            "UPDATE cf_agents_harness_operations SET answers = ?"
            " WHERE harness = ? AND session = ? AND id = ?",
            json.dumps([_answer_to_json(answer) for answer in answers]),
            self.harness,
            session,
            id,
        )

    def turn_owner(self, session: str, seq: int) -> str | None:
        """Return who sent the newest user message that ran, up to ``seq``."""
        rows = self.sql(
            "SELECT participant FROM cf_agents_harness_operations"
            " WHERE harness = ? AND session = ? AND seq <= ?"
            f" AND status != 'queued' AND {_IS_PROMPT} ORDER BY seq DESC LIMIT 1",
            self.harness,
            session,
            seq,
        )
        return rows[0]["participant"] if rows else None


def _input_to_json(input: HarnessInput) -> JSONValue:
    if isinstance(input, str):
        return {"prompt": input}
    if is_answers(input):
        return {"answers": [_answer_to_json(answer) for answer in input]}
    return {"parts": [_part_to_json(part) for part in input]}


def _input_from_json(data: dict[str, Any]) -> HarnessInput:
    if "prompt" in data:
        return data["prompt"]
    if "answers" in data:
        return [_answer_from_json(answer) for answer in data["answers"]]
    return [_part_from_json(part) for part in data["parts"]]


def _part_to_json(part: InputPart) -> JSONValue:
    if isinstance(part, TextInput):
        return {"type": "text", "text": part.text}
    return {
        "type": "file",
        "media_type": part.media_type,
        "url": part.url,
        "filename": part.filename,
    }


def _part_from_json(data: dict[str, Any]) -> InputPart:
    if data["type"] == "text":
        return TextInput(text=data["text"])
    return FileInput(
        media_type=data["media_type"], url=data["url"], filename=data["filename"]
    )


def _answer_to_json(answer: ToolAnswer) -> JSONValue:
    if isinstance(answer, Approval):
        return {
            "type": "approval",
            "id": answer.id,
            "approved": answer.approved,
            "message": answer.message,
        }
    return {"type": "output", "id": answer.id, "value": answer.value}


def _answer_from_json(data: dict[str, Any]) -> ToolAnswer:
    if data["type"] == "approval":
        return Approval(
            id=data["id"], approved=data["approved"], message=data["message"]
        )
    return ToolOutput(id=data["id"], value=data["value"])


def _request_to_json(request: ToolRequest) -> JSONValue:
    return {
        "id": request.id,
        "name": request.name,
        "args": request.args,
        "kind": request.kind,
    }
