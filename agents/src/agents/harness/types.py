"""Types for harnesses: inputs, turns, operation results, and events."""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from ..core.types import JSONValue
from .transcript import JSONObject, MessagePart, ResponseChunk, TranscriptMessage

__all__ = (
    "Approval",
    "ChunkEvent",
    "FileInput",
    "HarnessEvent",
    "HarnessInput",
    "InputPart",
    "MessageEvent",
    "OperationEvent",
    "OperationResult",
    "OperationStatus",
    "PendingOperation",
    "Receipt",
    "Replay",
    "ResetEvent",
    "RunEndEvent",
    "RunSnapshot",
    "RunStartEvent",
    "SaveCheckpoint",
    "SessionInfo",
    "SessionSnapshot",
    "TextEvent",
    "TextInput",
    "ToolAnswer",
    "ToolCallEvent",
    "ToolOutput",
    "ToolRequest",
    "ToolResultEvent",
    "ToolSpec",
    "Turn",
    "TurnResult",
    "WhenBusy",
)

type OperationStatus = Literal[
    "queued", "running", "done", "needs_input", "unanswered", "aborted"
]
"""``queued`` and ``running`` are open; the rest are settled. ``needs_input``
means the turn stopped on tool calls the client must answer with new
operations."""

type WhenBusy = Literal["queue", "reject"]
"""What ``submit`` does when the session has open work: queue behind it, or
raise `SessionBusyError`."""

type Replay = Literal["safe", "unsafe"]
"""Whether a tool may run again after a crash interrupted it."""


@dataclass(slots=True, frozen=True, kw_only=True)
class ToolSpec:
    """A ``@tool`` method bound to its host, as a harness receives it."""

    fn: Callable[..., Awaitable[Any]]
    name: str
    requires_approval: bool = False
    replay: Replay = "unsafe"


@dataclass(slots=True, frozen=True, kw_only=True)
class Approval:
    """The client's decision on a tool call that needs approval."""

    id: str
    approved: bool
    message: str | None = None


@dataclass(slots=True, frozen=True, kw_only=True)
class ToolOutput:
    """The result of a tool call the client ran itself."""

    id: str
    value: JSONValue


type ToolAnswer = Approval | ToolOutput
"""An answer to one of a ``needs_input`` result's requests."""


@dataclass(slots=True, frozen=True, kw_only=True)
class TextInput:
    """Text in a user message."""

    text: str


@dataclass(slots=True, frozen=True, kw_only=True)
class FileInput:
    """A file in a user message; ``url`` may be a ``data:`` URL."""

    media_type: str
    url: str
    filename: str | None = None


type InputPart = TextInput | FileInput
"""One part of a user message."""

type HarnessInput = str | Sequence[InputPart] | Sequence[ToolAnswer]
"""What an operation carries: a user message (text, or parts), or answers to
tool requests."""


@dataclass(slots=True, frozen=True, kw_only=True)
class ToolRequest:
    """A tool call the turn stopped on: approve it, or run it and send the output."""

    id: str
    name: str
    args: dict[str, JSONValue]
    kind: Literal["approval", "call"]


@dataclass(slots=True, frozen=True, kw_only=True)
class Receipt:
    """What ``submit`` returns once the operation is stored.

    ``accepted`` is ``False`` when an operation with this id already existed.
    """

    session: str
    operation_id: str
    accepted: bool


@dataclass(slots=True, frozen=True, kw_only=True)
class OperationResult:
    """A settled operation."""

    session: str
    operation_id: str
    status: OperationStatus
    text: str | None = None
    requests: Sequence[ToolRequest] = ()
    reason: str | None = None


@dataclass(slots=True, frozen=True, kw_only=True)
class PendingOperation:
    """An open operation."""

    session: str
    operation_id: str
    status: Literal["queued", "running"]


@dataclass(slots=True, frozen=True, kw_only=True)
class SessionInfo:
    """One session, as ``sessions.list()`` returns it."""

    id: str
    parent: str | None = None
    """The session this one was forked from."""
    busy: bool = False
    """Whether the session has an operation queued or running."""


@dataclass(slots=True, frozen=True, kw_only=True)
class RunSnapshot:
    """The run in progress when a watch started."""

    operations: Sequence[str]
    partial: Sequence[ResponseChunk] = ()
    """The in-flight reply so far, as chunks; parts still streaming are left
    open, and the watch's `ChunkEvent`s continue them."""


@dataclass(slots=True, frozen=True, kw_only=True)
class SessionSnapshot:
    """The first event ``watch`` yields: the session's state.

    ``messages`` is the transcript since the newest reset, without what the
    run in progress has added: that is ``run.partial``.
    """

    messages: Sequence[TranscriptMessage]
    pending: Sequence[PendingOperation]
    run: RunSnapshot | None = None
    type: Literal["snapshot"] = "snapshot"


@dataclass(slots=True, frozen=True, kw_only=True)
class OperationEvent:
    """An operation changed status."""

    operation_id: str
    status: OperationStatus
    type: Literal["operation"] = "operation"


@dataclass(slots=True, frozen=True, kw_only=True)
class RunStartEvent:
    """A turn started running (again, after a crash) for these operations."""

    operations: Sequence[str]
    type: Literal["run-start"] = "run-start"


@dataclass(slots=True, frozen=True, kw_only=True)
class RunEndEvent:
    """The run for these operations ended; its reply has been saved."""

    operations: Sequence[str]
    type: Literal["run-end"] = "run-end"


@dataclass(slots=True, frozen=True, kw_only=True)
class ChunkEvent:
    """A streamed piece of the in-flight reply. Part ids are unique per run."""

    operation_id: str
    chunk: ResponseChunk
    type: Literal["chunk"] = "chunk"


@dataclass(slots=True, frozen=True, kw_only=True)
class MessageEvent:
    """A message was saved to the transcript; it replaces any with its id."""

    message: TranscriptMessage
    type: Literal["message"] = "message"


@dataclass(slots=True, frozen=True, kw_only=True)
class ResetEvent:
    """The session was reset; messages that follow belong to the new context."""

    type: Literal["reset"] = "reset"


@dataclass(slots=True, frozen=True, kw_only=True)
class TextEvent:
    """Model text streamed during a turn."""

    operation_id: str
    text: str
    type: Literal["text"] = "text"


@dataclass(slots=True, frozen=True, kw_only=True)
class ToolCallEvent:
    """A tool started running."""

    operation_id: str
    tool_call_id: str
    name: str
    args: dict[str, JSONValue]
    type: Literal["tool-call"] = "tool-call"


@dataclass(slots=True, frozen=True, kw_only=True)
class ToolResultEvent:
    """A tool finished."""

    operation_id: str
    tool_call_id: str
    result: JSONValue
    is_error: bool = False
    type: Literal["tool-result"] = "tool-result"


type HarnessEvent = (
    SessionSnapshot
    | OperationEvent
    | RunStartEvent
    | RunEndEvent
    | ChunkEvent
    | MessageEvent
    | ResetEvent
    | TextEvent
    | ToolCallEvent
    | ToolResultEvent
)
"""What ``watch`` yields."""


class SaveCheckpoint(Protocol):
    """``Turn.save``: durably replace the turn's checkpoint and reply."""

    def __call__(
        self,
        checkpoint: bytes,
        /,
        *,
        reply: Sequence[MessagePart] | None = None,
        metadata: JSONObject | None = None,
    ) -> None: ...


@dataclass(slots=True, frozen=True, kw_only=True)
class Turn:
    """One turn for ``Harness.run_turn`` to run.

    Parameters
    ----------
    session
        The session id.
    operation_id
        The operation this turn answers.
    input
        The operation's user message, or every answer to the requests the
        turn continues from.
    history
        The session's history before this turn, as the subclass last returned
        it; ``None`` for a new session.
    checkpoint
        The last ``save``d bytes when resuming after a crash; ``None`` on a
        first run.
    attempt
        0 on a first run, then how many times the turn was interrupted.
    tools
        The host's ``@tool`` methods, bound.
    participant
        Who started the turn: the client tool calls it makes are theirs.
    emit
        Publishes an event to the session's watchers; a `ChunkEvent` also
        becomes part of the in-flight reply ``watch`` snapshots show.
    save
        Durably replaces the turn's checkpoint and, with ``reply``, the
        assistant message's parts so far, in one transaction.
    """

    session: str
    operation_id: str
    input: HarnessInput
    history: bytes | None
    checkpoint: bytes | None
    attempt: int
    tools: Sequence[ToolSpec]
    participant: str | None
    emit: Callable[[HarnessEvent], None]
    save: SaveCheckpoint


@dataclass(slots=True, frozen=True, kw_only=True)
class TurnResult:
    """What ``Harness.run_turn`` returns.

    ``history`` replaces the session's, and ``reply`` (with ``metadata``)
    replaces the turn's assistant message, in one transaction.
    """

    history: bytes
    reply: Sequence[MessagePart] = ()
    metadata: JSONObject | None = None
    status: Literal["done", "needs_input"] = "done"
    text: str | None = None
    requests: Sequence[ToolRequest] = ()


@dataclass(slots=True, kw_only=True)
class ActiveRun:
    """The run in progress in one session, in memory only."""

    operation_id: str
    message_id: str
    """The assistant message the run writes."""
    before: TranscriptMessage | None
    """That message as it was when the run started."""
    partial: list[ResponseChunk] = field(default_factory=list)
    wrote_reply: bool = False
