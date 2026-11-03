"""Channels protocol records: participants, turns and inbound events.

Port of upstream ``experimental/channels/protocol.ts``. Records follow
``.design/utilities.md`` §5: snake_case attributes, camelCase wire names, and a
class-constant discriminator per union (``type``, ``status`` or ``ok``).
"""

# TODO(phase 0a): re-export the transcript records (TranscriptMessage, its
# parts, ResponseChunk) from agents.harness.transcript once that module lands,
# and replace WireMessage and WireChunk with them. Until then, messages and
# chunks are carried in wire form.

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import ClassVar, Literal

from typing_extensions import TypeIs

from ..core.records import wire
from ..core.types import JSONValue

__all__ = (
    "ApprovalResponse",
    "Cancel",
    "ClientTool",
    "ConversationCreate",
    "ConversationFork",
    "ConversationInfo",
    "ConversationOperation",
    "ConversationReset",
    "ConversationSnapshot",
    "InboundEvent",
    "InboundMessage",
    "Participant",
    "QueuedTurn",
    "RunningTurn",
    "SettledTurn",
    "ToolResult",
    "ToolResultError",
    "ToolResultOk",
    "TurnOutcome",
    "TurnStatus",
    "WireChunk",
    "WireMessage",
    "parse_inbound_event",
)

type WireMessage = dict[str, JSONValue]
"""A transcript message in wire form (upstream ``TranscriptMessage``)."""

type WireChunk = dict[str, JSONValue]
"""A response chunk in wire form (upstream ``ResponseChunk``)."""


# Participants


@dataclass(slots=True, kw_only=True)
class Participant:
    """A person or client acting in a conversation.

    Parameters
    ----------
    id
        Stable within the conversation; what the agent compares for ownership.
    name
        A display name.
    """

    id: str
    name: str | None = None


# Turns

type TurnOutcome = Literal["completed", "failed", "aborted", "awaiting-input"]
"""How a settled turn ended."""


@dataclass(slots=True, kw_only=True)
class QueuedTurn:
    """A turn waiting for the conversation's running turn to settle.

    Parameters
    ----------
    turn_id
        The turn's id.
    started_by
        Event id of the inbound message that started the turn.
    """

    turn_id: str = wire("turnId")
    started_by: str = wire("startedBy")
    status: ClassVar[str] = "queued"


@dataclass(slots=True, kw_only=True)
class RunningTurn:
    """A turn whose output streams as a response.

    Parameters
    ----------
    turn_id
        The turn's id.
    started_by
        Event id of the inbound message that started the turn.
    response_id
        The response carrying this run's output.
    extends
        The saved assistant message the response continues.
    """

    turn_id: str = wire("turnId")
    started_by: str = wire("startedBy")
    response_id: str = wire("responseId")
    extends: str | None = None
    status: ClassVar[str] = "running"


@dataclass(slots=True, kw_only=True)
class SettledTurn:
    """A turn that has finished, for now.

    Parameters
    ----------
    turn_id
        The turn's id.
    started_by
        Event id of the inbound message that started the turn.
    outcome
        How it ended. An ``awaiting-input`` turn reopens on a tool answer.
    message_ids
        The transcript messages the turn wrote.
    error
        Shown to participants, only with outcome ``failed``. Channels sends a
        generic message when it is absent.
    """

    turn_id: str = wire("turnId")
    started_by: str = wire("startedBy")
    outcome: TurnOutcome
    message_ids: list[str] = wire("messageIds")
    error: str | None = None
    status: ClassVar[str] = "settled"


type TurnStatus = QueuedTurn | RunningTurn | SettledTurn
"""A turn's status, discriminated by ``status``."""


# Snapshots


@dataclass(slots=True, kw_only=True)
class ConversationSnapshot:
    """The transcript plus every open turn.

    Parameters
    ----------
    messages
        The transcript, in order.
    turns
        Every turn that is queued, running or awaiting input.
    """

    messages: list[WireMessage] = field(default_factory=list)
    turns: list[TurnStatus] = field(default_factory=list)


# Inbound events


@dataclass(slots=True, kw_only=True)
class ClientTool:
    """A tool a participant's client can run, advertised with an inbound event.

    Accepted on the wire and not used yet.

    Parameters
    ----------
    name
        The tool's name.
    description
        What the tool does, for the model.
    input_schema
        JSON Schema of the tool's input.
    """

    name: str
    description: str | None = None
    input_schema: dict[str, JSONValue] | None = wire("inputSchema", None)


@dataclass(slots=True, kw_only=True)
class InboundMessage:
    """A participant's message, which starts a turn.

    Parameters
    ----------
    event_id
        Stable across redelivery of the same event.
    message
        The user message, saved under its own ``id``.
    client_tools
        Tools the sender's client can run.
    """

    event_id: str = wire("eventId")
    message: WireMessage
    client_tools: list[ClientTool] | None = wire("clientTools", None)
    type: ClassVar[str] = "message"


@dataclass(slots=True, kw_only=True)
class ToolResultOk:
    """A client tool's output.

    Parameters
    ----------
    output
        The output; ``None`` is JSON ``null``.
    """

    output: JSONValue
    ok: ClassVar[bool] = True


@dataclass(slots=True, kw_only=True)
class ToolResultError:
    """A client tool's failure.

    Parameters
    ----------
    error_text
        Why it failed.
    """

    error_text: str | None = wire("errorText", None)
    ok: ClassVar[bool] = False


@dataclass(slots=True, kw_only=True)
class ToolResult:
    """The answer to a client tool call a turn is waiting on.

    Parameters
    ----------
    event_id
        Stable across redelivery of the same event.
    turn_id
        The turn waiting on the call.
    tool_call_id
        The call answered.
    result
        Its output or failure.
    client_tools
        Tools the sender's client can run.
    """

    event_id: str = wire("eventId")
    turn_id: str = wire("turnId")
    tool_call_id: str = wire("toolCallId")
    result: ToolResultOk | ToolResultError
    client_tools: list[ClientTool] | None = wire("clientTools", None)
    type: ClassVar[str] = "tool-result"


@dataclass(slots=True, kw_only=True)
class ApprovalResponse:
    """A participant's decision on a tool call awaiting approval.

    Parameters
    ----------
    event_id
        Stable across redelivery of the same event.
    turn_id
        The turn waiting on the approval.
    approval_id
        The approval answered.
    approved
        Whether the call may run.
    reason
        Why, for the model.
    """

    event_id: str = wire("eventId")
    turn_id: str = wire("turnId")
    approval_id: str = wire("approvalId")
    approved: bool
    reason: str | None = None
    type: ClassVar[str] = "approval-response"


@dataclass(slots=True, kw_only=True)
class Cancel:
    """Stop a turn.

    Parameters
    ----------
    event_id
        Stable across redelivery of the same event.
    turn_id
        The turn to stop.
    """

    event_id: str = wire("eventId")
    turn_id: str = wire("turnId")
    type: ClassVar[str] = "cancel"


@dataclass(slots=True, kw_only=True)
class ConversationCreate:
    """Start a new, empty conversation. The sending surface follows it.

    Parameters
    ----------
    event_id
        Stable across redelivery of the same event.
    """

    event_id: str = wire("eventId")
    type: ClassVar[str] = "conversation-create"


@dataclass(slots=True, kw_only=True)
class ConversationFork:
    """Start a new conversation with this one's transcript so far.

    The sending surface follows it; other surfaces stay.

    Parameters
    ----------
    event_id
        Stable across redelivery of the same event.
    """

    event_id: str = wire("eventId")
    type: ClassVar[str] = "conversation-fork"


@dataclass(slots=True, kw_only=True)
class ConversationReset:
    """Start this conversation over, keeping its id.

    Parameters
    ----------
    event_id
        Stable across redelivery of the same event.
    handoff
        A note the agent may carry into the new context.
    """

    event_id: str = wire("eventId")
    handoff: str | None = None
    type: ClassVar[str] = "conversation-reset"


type InboundEvent = (
    InboundMessage
    | ToolResult
    | ApprovalResponse
    | Cancel
    | ConversationCreate
    | ConversationFork
    | ConversationReset
)
"""What an interface sends, discriminated by ``type``."""

type ConversationOperation = Literal[
    "conversation-create", "conversation-fork", "conversation-reset"
]
"""Inbound events that act on a conversation rather than in it."""


@dataclass(slots=True, kw_only=True)
class ConversationInfo:
    """One of an agent's conversations, as a client lists them.

    Parameters
    ----------
    id
        The conversation's id.
    busy
        Whether a turn is running or queued.
    parent
        The conversation this one was forked from.
    """

    id: str
    busy: bool
    parent: str | None = None


# Parsing: port of upstream web/protocol.ts parseEvent. The wire is untrusted,
# so every field is checked and the first bad one is named.


def parse_inbound_event(value: object) -> InboundEvent | str:
    """Parse an inbound event from its wire form.

    Parameters
    ----------
    value
        Decoded JSON.

    Returns
    -------
    InboundEvent | str
        The event, or the wire name of the first invalid field (``eventId``,
        ``message.parts``, ``type``, ...), which the Web Channel reports as
        ``Invalid frame: <field>``.
    """
    if not isinstance(value, dict):
        return "eventId"
    event_id = value.get("eventId")
    if not _is_id(event_id):
        return "eventId"
    match value.get("type"):
        case "message":
            return _parse_message(value, event_id)
        case "tool-result":
            return _parse_tool_result(value, event_id)
        case "approval-response":
            return _parse_approval_response(value, event_id)
        case "cancel":
            turn_id = value.get("turnId")
            if not _is_id(turn_id):
                return "turnId"
            return Cancel(event_id=event_id, turn_id=turn_id)
        case "conversation-create":
            return ConversationCreate(event_id=event_id)
        case "conversation-fork":
            return ConversationFork(event_id=event_id)
        case "conversation-reset":
            handoff = value.get("handoff", "")
            if not isinstance(handoff, str):
                return "handoff"
            return ConversationReset(event_id=event_id, handoff=handoff or None)
        case _:
            return "type"


def _parse_message(event: Mapping[str, JSONValue], event_id: str) -> InboundEvent | str:
    message = event.get("message")
    if not isinstance(message, dict) or not _is_id(message.get("id")):
        return "message.id"
    if message.get("role") != "user":
        return "message.role"
    parts = message.get("parts")
    # A part needs only a string type: the agent decides which types it accepts.
    if not isinstance(parts, list) or not all(
        isinstance(part, dict) and isinstance(part.get("type"), str) for part in parts
    ):
        return "message.parts"
    parsed: WireMessage = {"id": message["id"], "role": "user", "parts": parts}
    metadata = message.get("metadata")
    if isinstance(metadata, dict):
        parsed["metadata"] = metadata
    return InboundMessage(
        event_id=event_id,
        message=parsed,
        client_tools=_client_tools(event.get("clientTools")),
    )


def _parse_tool_result(
    event: Mapping[str, JSONValue], event_id: str
) -> InboundEvent | str:
    turn_id = event.get("turnId")
    tool_call_id = event.get("toolCallId")
    result = event.get("result")
    if not _is_id(turn_id):
        return "turnId"
    if not _is_id(tool_call_id):
        return "toolCallId"
    if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
        return "result"
    error_text = result.get("errorText")
    return ToolResult(
        event_id=event_id,
        turn_id=turn_id,
        tool_call_id=tool_call_id,
        result=ToolResultOk(output=result.get("output"))
        if result["ok"]
        else ToolResultError(
            error_text=error_text if isinstance(error_text, str) else None
        ),
        client_tools=_client_tools(event.get("clientTools")),
    )


def _parse_approval_response(
    event: Mapping[str, JSONValue], event_id: str
) -> InboundEvent | str:
    turn_id = event.get("turnId")
    approval_id = event.get("approvalId")
    approved = event.get("approved")
    reason = event.get("reason")
    if not _is_id(turn_id):
        return "turnId"
    if not _is_id(approval_id):
        return "approvalId"
    if not isinstance(approved, bool):
        return "approved"
    return ApprovalResponse(
        event_id=event_id,
        turn_id=turn_id,
        approval_id=approval_id,
        approved=approved,
        reason=reason if isinstance(reason, str) else None,
    )


def _client_tools(value: JSONValue) -> list[ClientTool] | None:
    # Keeps tools with a name, and drops a description or schema of the wrong
    # type rather than the tool.
    if not isinstance(value, list):
        return None
    tools: list[ClientTool] = []
    for tool in value:
        if not isinstance(tool, dict):
            continue
        name = tool.get("name")
        if not _is_id(name):
            continue
        description = tool.get("description")
        schema = tool.get("inputSchema")
        tools.append(
            ClientTool(
                name=name,
                description=description if isinstance(description, str) else None,
                input_schema=schema if isinstance(schema, dict) else None,
            )
        )
    return tools


def _is_id(value: object) -> TypeIs[str]:
    return isinstance(value, str) and value != ""
