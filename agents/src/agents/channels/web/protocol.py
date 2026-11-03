"""Frames between Channels and a connected Web Channel client.

Port of upstream ``experimental/channels/web/protocol.ts``. Frames are JSON
text, every type starts with ``channels:``, and every server frame names its
conversation, so a client can drop frames for a conversation it no longer
follows. Upstream's ``from`` field is ``start`` here, as in ``Streams``.
"""

import json
from dataclasses import dataclass, field
from typing import ClassVar, Literal

from ...core.records import wire
from ...core.types import JSONValue
from .._wire import record_to_wire
from ..protocol import (
    ConversationInfo,
    ConversationOperation,
    InboundEvent,
    Participant,
    ResponseChunk,
    TranscriptMessage,
    TurnStatus,
    parse_inbound_event,
)

__all__ = (
    "AckFrame",
    "CaughtUpFrame",
    "ChunksFrame",
    "ClientFrame",
    "ConversationsFrame",
    "EndFrame",
    "EventFrame",
    "InvalidClientFrame",
    "ListConversationsFrame",
    "MessagesFrame",
    "ServerFrame",
    "SnapshotFrame",
    "SubscribeFrame",
    "TurnFrame",
    "frame_to_wire",
    "parse_client_frame",
)


# Client to server


@dataclass(slots=True, kw_only=True)
class EventFrame:
    """An inbound event, answered by ``channels:ack``.

    Parameters
    ----------
    event
        The event.
    """

    event: InboundEvent
    type: ClassVar[str] = "channels:event"


@dataclass(slots=True, kw_only=True)
class SubscribeFrame:
    """Read a response: its replay, then its live tail.

    Parameters
    ----------
    response_id
        The response to read.
    start
        The first chunk's seq, inclusive. Default: the beginning.
    """

    response_id: str = wire("responseId")
    start: int | None = wire("from", None)
    type: ClassVar[str] = "channels:subscribe"


@dataclass(slots=True, kw_only=True)
class ListConversationsFrame:
    """Ask for the agent's conversations, answered by ``channels:conversations``.

    Parameters
    ----------
    request_id
        Echoed on the answer.
    """

    request_id: str = wire("requestId")
    type: ClassVar[str] = "channels:list-conversations"


type ClientFrame = EventFrame | SubscribeFrame | ListConversationsFrame
"""A frame a client sends, discriminated by ``type``."""


@dataclass(slots=True, kw_only=True)
class InvalidClientFrame:
    """A frame that names a Channels type but is malformed.

    The Web Channel answers it with ``channels:ack`` carrying
    ``Invalid frame: <invalid>``.

    Parameters
    ----------
    invalid
        The wire name of the first invalid field.
    event_id
        The event's id, when the frame carried a valid one.
    """

    invalid: str
    event_id: str | None = None


# Server to client


@dataclass(slots=True, kw_only=True)
class SnapshotFrame:
    """The conversation's transcript and open turns.

    Sent on connect, when the connection follows another conversation, and
    (empty) after a reset.

    Parameters
    ----------
    conversation_id
        The conversation.
    you
        This connection's participant.
    operations
        The conversation operations this connection may send.
    messages
        The transcript, in order.
    turns
        Every turn that is queued, running or awaiting input.
    """

    conversation_id: str = wire("conversationId")
    you: Participant
    operations: list[ConversationOperation] = field(default_factory=list)
    messages: list[TranscriptMessage] = field(default_factory=list)
    turns: list[TurnStatus] = field(default_factory=list)
    type: ClassVar[str] = "channels:snapshot"


@dataclass(slots=True, kw_only=True)
class TurnFrame:
    """A turn's status changed.

    Parameters
    ----------
    conversation_id
        The conversation.
    turn
        The turn's new status.
    """

    conversation_id: str = wire("conversationId")
    turn: TurnStatus
    type: ClassVar[str] = "channels:turn"


@dataclass(slots=True, kw_only=True)
class MessagesFrame:
    """Transcript messages to upsert by id; an unknown id appends.

    Parameters
    ----------
    conversation_id
        The conversation.
    messages
        The messages.
    """

    conversation_id: str = wire("conversationId")
    messages: list[TranscriptMessage]
    type: ClassVar[str] = "channels:messages"


@dataclass(slots=True, kw_only=True)
class ChunksFrame:
    """A page of a subscribed response.

    Parameters
    ----------
    conversation_id
        The conversation.
    response_id
        The response.
    start
        The first chunk's seq.
    chunks
        The chunks, in order.
    """

    conversation_id: str = wire("conversationId")
    response_id: str = wire("responseId")
    start: int = wire("from")
    chunks: list[ResponseChunk]
    type: ClassVar[str] = "channels:chunks"


@dataclass(slots=True, kw_only=True)
class CaughtUpFrame:
    """Everything before this was replay; what follows is live.

    Parameters
    ----------
    conversation_id
        The conversation.
    response_id
        The response.
    cursor
        The seq the live tail starts at.
    """

    conversation_id: str = wire("conversationId")
    response_id: str = wire("responseId")
    cursor: int
    type: ClassVar[str] = "channels:caught-up"


@dataclass(slots=True, kw_only=True)
class EndFrame:
    """A subscribed response is over.

    Parameters
    ----------
    conversation_id
        The conversation.
    response_id
        The response.
    state
        ``ended`` when its producer closed it, ``interrupted`` when it was
        cut off, ``not-found`` when it doesn't exist.
    """

    conversation_id: str = wire("conversationId")
    response_id: str = wire("responseId")
    state: Literal["ended", "interrupted", "not-found"]
    type: ClassVar[str] = "channels:end"


@dataclass(slots=True, kw_only=True)
class ConversationsFrame:
    """The agent's conversations.

    The answer to a list request, carrying its ``request_id``, or pushed when
    a conversation is created or forked.

    Parameters
    ----------
    conversation_id
        The conversation the connection follows.
    request_id
        The list request answered.
    conversations
        The conversations, when listing succeeded.
    error
        Why listing failed.
    """

    conversation_id: str = wire("conversationId")
    request_id: str | None = wire("requestId", None)
    conversations: list[ConversationInfo] | None = None
    error: str | None = None
    type: ClassVar[str] = "channels:conversations"


@dataclass(slots=True, kw_only=True)
class AckFrame:
    """The answer to an inbound event.

    Parameters
    ----------
    conversation_id
        The conversation; for a create or fork, the one it started.
    event_id
        The event answered, ``""`` when the frame carried none.
    error
        Why the event was refused.
    """

    conversation_id: str = wire("conversationId")
    event_id: str = wire("eventId")
    error: str | None = None
    type: ClassVar[str] = "channels:ack"


type ServerFrame = (
    SnapshotFrame
    | TurnFrame
    | MessagesFrame
    | ChunksFrame
    | CaughtUpFrame
    | EndFrame
    | ConversationsFrame
    | AckFrame
)
"""A frame Channels sends, discriminated by ``type``."""


def frame_to_wire(frame: ClientFrame | ServerFrame) -> dict[str, JSONValue]:
    """Return a frame's wire form, ready for ``json.dumps``.

    Optional fields that are ``None`` are left out, as upstream sends them.
    """
    return record_to_wire(frame)


def parse_client_frame(text: str) -> ClientFrame | InvalidClientFrame | None:
    """Parse a text frame from a client.

    Returns
    -------
    ClientFrame | InvalidClientFrame | None
        The frame; an ``InvalidClientFrame`` for a malformed frame of a
        Channels type; ``None`` for anything else, so the socket's other
        protocols keep working.
    """
    try:
        frame = json.loads(text)
    except ValueError:
        return None
    if not isinstance(frame, dict):
        return None
    match frame.get("type"):
        case "channels:subscribe":
            return _parse_subscribe(frame)
        case "channels:list-conversations":
            request_id = frame.get("requestId")
            if not isinstance(request_id, str) or request_id == "":
                return InvalidClientFrame(invalid="requestId")
            return ListConversationsFrame(request_id=request_id)
        case "channels:event":
            event = parse_inbound_event(frame.get("event"))
            if not isinstance(event, str):
                return EventFrame(event=event)
            if event == "eventId":
                return InvalidClientFrame(invalid=event)
            return InvalidClientFrame(invalid=event, event_id=frame["event"]["eventId"])
        case _:
            return None


def _parse_subscribe(
    frame: dict[str, JSONValue],
) -> SubscribeFrame | InvalidClientFrame:
    response_id = frame.get("responseId")
    if not isinstance(response_id, str):
        return InvalidClientFrame(invalid="responseId")
    if "from" not in frame:
        return SubscribeFrame(response_id=response_id)
    start = frame["from"]
    # Number.isInteger: JSON 2.0 is the integer 2, and a boolean is not a number.
    if isinstance(start, float) and start.is_integer():
        start = int(start)
    if not isinstance(start, int) or isinstance(start, bool):
        return InvalidClientFrame(invalid="from")
    return SubscribeFrame(response_id=response_id, start=start)
