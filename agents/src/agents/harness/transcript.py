"""Transcript records: the messages a harness keeps and the chunks it streams.

A port of upstream's ``experimental/channels/protocol.ts`` transcript and
response types, and of ``apply-chunks.ts`` and the chunk grammar in
``response.ts`` (cloudflare/agents ``455b47e``). They live here rather than in
Channels because the harness owns the transcript and must not depend on
Channels.

The records are not `agents.chat`'s ``UIMessage`` classes: a tool part is one
``type: "tool"`` class with a ``state`` and an ``owner``, and a response has no
start or finish chunk. Import this module by its path
(``from agents.harness.transcript import TranscriptMessage``); its names would
collide with chat's in the root ``agents`` namespace.
"""

import dataclasses
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from typing import Any, ClassVar, Literal, NamedTuple

from ..core.records import wire
from ..core.types import JSONValue
from .errors import ResponseGrammarError

__all__ = (
    "Data",
    "DataPart",
    "FilePart",
    "JSONObject",
    "MessagePart",
    "Metadata",
    "ReasoningDelta",
    "ReasoningEnd",
    "ReasoningPart",
    "ReasoningStart",
    "ResponseChunk",
    "ResponseGrammar",
    "ResponseGrammarError",
    "SourceDocumentPart",
    "SourceUrlPart",
    "TextDelta",
    "TextEnd",
    "TextPart",
    "TextStart",
    "ToolApproval",
    "ToolApprovalRequest",
    "ToolInputAvailable",
    "ToolInputDelta",
    "ToolInputError",
    "ToolInputStart",
    "ToolOutputAvailable",
    "ToolOutputDenied",
    "ToolOutputError",
    "ToolPart",
    "ToolState",
    "TranscriptMessage",
    "apply_chunks",
    "chunk_from_wire",
    "message_from_wire",
    "part_from_wire",
    "to_wire",
)

type JSONObject = dict[str, JSONValue]
"""A JSON object (upstream's ``JsonObject``)."""

type ToolState = Literal[
    "input-streaming",
    "input-available",
    "approval-requested",
    "approval-responded",
    "output-available",
    "output-error",
    "output-denied",
]
"""Where a tool call is: its input streaming in, waiting, or finished."""


# Message parts


@dataclass(slots=True, kw_only=True)
class TextPart:
    """Text in a message."""

    type: ClassVar[str] = "text"
    text: str
    provider_metadata: JSONObject | None = wire("providerMetadata", None)


@dataclass(slots=True, kw_only=True)
class ReasoningPart:
    """The model's reasoning."""

    type: ClassVar[str] = "reasoning"
    text: str
    provider_metadata: JSONObject | None = wire("providerMetadata", None)


@dataclass(slots=True, kw_only=True)
class FilePart:
    """A file, in a message or as a chunk; ``url`` may be a ``data:`` URL."""

    type: ClassVar[str] = "file"
    media_type: str = wire("mediaType")
    url: str
    filename: str | None = None
    provider_metadata: JSONObject | None = wire("providerMetadata", None)


@dataclass(slots=True, kw_only=True)
class SourceUrlPart:
    """A cited web page, in a message or as a chunk."""

    type: ClassVar[str] = "source-url"
    source_id: str = wire("sourceId")
    url: str
    title: str | None = None
    provider_metadata: JSONObject | None = wire("providerMetadata", None)


@dataclass(slots=True, kw_only=True)
class SourceDocumentPart:
    """A cited document, in a message or as a chunk."""

    type: ClassVar[str] = "source-document"
    source_id: str = wire("sourceId")
    media_type: str = wire("mediaType")
    title: str
    filename: str | None = None
    provider_metadata: JSONObject | None = wire("providerMetadata", None)


@dataclass(slots=True, kw_only=True)
class DataPart:
    """Application data in a message.

    A later part with the same ``name`` and a set ``id`` replaces it.
    """

    type: ClassVar[str] = "data"
    name: str
    id: str | None = None
    data: JSONValue


@dataclass(slots=True, kw_only=True)
class ToolApproval:
    """A tool call's approval request, and the decision once there is one."""

    id: str
    approved: bool | None = None
    reason: str | None = None


@dataclass(slots=True, kw_only=True)
class ToolPart:
    """One tool call and, once it has finished, its result.

    ``owner`` is the participant whose client may run a client tool call; it
    is ``None`` for tools the agent runs.
    """

    type: ClassVar[str] = "tool"
    tool_call_id: str = wire("toolCallId")
    tool_name: str = wire("toolName")
    state: ToolState
    input: JSONValue = None
    output: JSONValue = None
    error_text: str | None = wire("errorText", None)
    preliminary: bool | None = None
    approval: ToolApproval | None = None
    owner: str | None = None
    provider_executed: bool | None = wire("providerExecuted", None)
    dynamic: bool | None = None
    title: str | None = None
    provider_metadata: JSONObject | None = wire("providerMetadata", None)


type MessagePart = (
    TextPart
    | ReasoningPart
    | FilePart
    | SourceUrlPart
    | SourceDocumentPart
    | DataPart
    | ToolPart
)
"""One part of a `TranscriptMessage`."""


@dataclass(slots=True, kw_only=True)
class TranscriptMessage:
    """One message in a session's transcript."""

    id: str
    role: Literal["system", "user", "assistant"]
    parts: list[MessagePart]
    metadata: JSONObject | None = None


# Response chunks


@dataclass(slots=True, kw_only=True)
class TextStart:
    """Opens a text part."""

    type: ClassVar[str] = "text-start"
    id: str
    provider_metadata: JSONObject | None = wire("providerMetadata", None)


@dataclass(slots=True, kw_only=True)
class TextDelta:
    """Appends to an open text part."""

    type: ClassVar[str] = "text-delta"
    id: str
    delta: str


@dataclass(slots=True, kw_only=True)
class TextEnd:
    """Closes a text part."""

    type: ClassVar[str] = "text-end"
    id: str
    provider_metadata: JSONObject | None = wire("providerMetadata", None)


@dataclass(slots=True, kw_only=True)
class ReasoningStart:
    """Opens a reasoning part."""

    type: ClassVar[str] = "reasoning-start"
    id: str
    provider_metadata: JSONObject | None = wire("providerMetadata", None)


@dataclass(slots=True, kw_only=True)
class ReasoningDelta:
    """Appends to an open reasoning part."""

    type: ClassVar[str] = "reasoning-delta"
    id: str
    delta: str


@dataclass(slots=True, kw_only=True)
class ReasoningEnd:
    """Closes a reasoning part."""

    type: ClassVar[str] = "reasoning-end"
    id: str
    provider_metadata: JSONObject | None = wire("providerMetadata", None)


@dataclass(slots=True, kw_only=True)
class ToolInputStart:
    """Opens a tool call whose input streams in."""

    type: ClassVar[str] = "tool-input-start"
    tool_call_id: str = wire("toolCallId")
    tool_name: str = wire("toolName")
    owner: str | None = None
    provider_executed: bool | None = wire("providerExecuted", None)
    dynamic: bool | None = None
    title: str | None = None


@dataclass(slots=True, kw_only=True)
class ToolInputDelta:
    """More of an open tool call's input, as JSON text."""

    type: ClassVar[str] = "tool-input-delta"
    tool_call_id: str = wire("toolCallId")
    delta: str


@dataclass(slots=True, kw_only=True)
class ToolInputAvailable:
    """A tool call's whole input; closes its tool input, open or not."""

    type: ClassVar[str] = "tool-input-available"
    tool_call_id: str = wire("toolCallId")
    tool_name: str = wire("toolName")
    input: JSONValue
    owner: str | None = None
    provider_executed: bool | None = wire("providerExecuted", None)
    dynamic: bool | None = None
    title: str | None = None
    provider_metadata: JSONObject | None = wire("providerMetadata", None)


@dataclass(slots=True, kw_only=True)
class ToolInputError:
    """A tool call whose input isn't valid; closes its tool input, open or not."""

    type: ClassVar[str] = "tool-input-error"
    tool_call_id: str = wire("toolCallId")
    tool_name: str = wire("toolName")
    input: JSONValue
    error_text: str = wire("errorText")
    provider_executed: bool | None = wire("providerExecuted", None)
    dynamic: bool | None = None
    title: str | None = None


@dataclass(slots=True, kw_only=True)
class ToolApprovalRequest:
    """A tool call waits for approval."""

    type: ClassVar[str] = "tool-approval-request"
    tool_call_id: str = wire("toolCallId")
    approval_id: str = wire("approvalId")


@dataclass(slots=True, kw_only=True)
class ToolOutputAvailable:
    """A tool call's output; a ``preliminary`` one is replaced by the next."""

    type: ClassVar[str] = "tool-output-available"
    tool_call_id: str = wire("toolCallId")
    output: JSONValue
    preliminary: bool | None = None
    provider_executed: bool | None = wire("providerExecuted", None)


@dataclass(slots=True, kw_only=True)
class ToolOutputError:
    """A tool call failed."""

    type: ClassVar[str] = "tool-output-error"
    tool_call_id: str = wire("toolCallId")
    error_text: str = wire("errorText")
    provider_executed: bool | None = wire("providerExecuted", None)


@dataclass(slots=True, kw_only=True)
class ToolOutputDenied:
    """A tool call was denied approval."""

    type: ClassVar[str] = "tool-output-denied"
    tool_call_id: str = wire("toolCallId")


@dataclass(slots=True, kw_only=True)
class Data:
    """Application data; a ``transient`` chunk is shown live and not kept."""

    type: ClassVar[str] = "data"
    name: str
    id: str | None = None
    data: JSONValue
    transient: bool | None = None


@dataclass(slots=True, kw_only=True)
class Metadata:
    """Merged into the metadata of the message the response builds."""

    type: ClassVar[str] = "metadata"
    metadata: JSONObject


type ResponseChunk = (
    TextStart
    | TextDelta
    | TextEnd
    | ReasoningStart
    | ReasoningDelta
    | ReasoningEnd
    | ToolInputStart
    | ToolInputDelta
    | ToolInputAvailable
    | ToolInputError
    | ToolApprovalRequest
    | ToolOutputAvailable
    | ToolOutputError
    | ToolOutputDenied
    | FilePart
    | SourceUrlPart
    | SourceDocumentPart
    | Data
    | Metadata
)
"""One streamed piece of a response. A response has no start or finish chunk:
it begins when it is opened and ends when it is closed."""

type _Record = TranscriptMessage | MessagePart | ResponseChunk | ToolApproval

_PARTS: dict[str, type[Any]] = {
    cls.type: cls
    for cls in (
        TextPart,
        ReasoningPart,
        FilePart,
        SourceUrlPart,
        SourceDocumentPart,
        DataPart,
        ToolPart,
    )
}
_CHUNKS: dict[str, type[Any]] = {
    cls.type: cls
    for cls in (
        TextStart,
        TextDelta,
        TextEnd,
        ReasoningStart,
        ReasoningDelta,
        ReasoningEnd,
        ToolInputStart,
        ToolInputDelta,
        ToolInputAvailable,
        ToolInputError,
        ToolApprovalRequest,
        ToolOutputAvailable,
        ToolOutputError,
        ToolOutputDenied,
        FilePart,
        SourceUrlPart,
        SourceDocumentPart,
        Data,
        Metadata,
    )
}
_ROLES = frozenset({"system", "user", "assistant"})


# Wire form


class _Field(NamedTuple):
    name: str
    wire: str
    required: bool


@cache
def _fields(cls: type[Any]) -> tuple[_Field, ...]:
    return tuple(
        _Field(
            name=f.name,
            wire=f.metadata.get("wire", f.name),
            required=f.default is dataclasses.MISSING,
        )
        for f in dataclasses.fields(cls)
    )


def to_wire(record: _Record) -> dict[str, JSONValue]:
    """Return a record's JSON form: camelCase keys, ``None`` optionals left out.

    A tool part in state ``output-available`` always carries ``output``,
    since ``null`` is a legitimate tool result.
    """
    if isinstance(record, TranscriptMessage):
        message: dict[str, JSONValue] = {
            "id": record.id,
            "role": record.role,
            "parts": [to_wire(part) for part in record.parts],
        }
        if record.metadata is not None:
            message["metadata"] = record.metadata
        return message

    out: dict[str, JSONValue] = {}
    if not isinstance(record, ToolApproval):
        out["type"] = record.type
    for f in _fields(type(record)):
        value = getattr(record, f.name)
        if isinstance(value, ToolApproval):
            out[f.wire] = to_wire(value)
        elif value is not None or f.required or _keeps_none(record, f.name):
            out[f.wire] = value
    return out


def _keeps_none(record: _Record, name: str) -> bool:
    return (
        isinstance(record, ToolPart)
        and name == "output"
        and record.state == "output-available"
    )


def message_from_wire(data: Mapping[str, Any]) -> TranscriptMessage:
    """Parse a `TranscriptMessage` from its JSON form.

    Raises
    ------
    ValueError
        If a required field is missing, or the role or a part type is unknown.
    """
    role = _require(data, "role")
    if role not in _ROLES:
        raise ValueError(f"Unknown message role: {role!r}")

    parts = _require(data, "parts")
    if not isinstance(parts, list):
        raise ValueError("A message's 'parts' must be a list")

    return TranscriptMessage(
        id=_require(data, "id"),
        role=role,
        parts=[part_from_wire(part) for part in parts],
        metadata=data.get("metadata"),
    )


def part_from_wire(data: Mapping[str, Any]) -> MessagePart:
    """Parse a `MessagePart` from its JSON form.

    Raises
    ------
    ValueError
        If the type is unknown or a required field is missing.
    """
    return _record_from_wire(_PARTS, data)


def chunk_from_wire(data: Mapping[str, Any]) -> ResponseChunk:
    """Parse a `ResponseChunk` from its JSON form.

    Raises
    ------
    ValueError
        If the type is unknown or a required field is missing.
    """
    return _record_from_wire(_CHUNKS, data)


def _record_from_wire(kinds: Mapping[str, type[Any]], data: Mapping[str, Any]) -> Any:
    kind = _require(data, "type")
    cls = kinds.get(kind)
    if cls is None:
        raise ValueError(f"Unknown type: {kind!r}")

    values: dict[str, Any] = {}
    for f in _fields(cls):
        if f.wire not in data:
            if f.required:
                raise ValueError(f"{kind!r} needs {f.wire!r}")
            continue

        value = data[f.wire]
        if f.name == "approval" and value is not None:
            value = ToolApproval(
                id=_require(value, "id"),
                approved=value.get("approved"),
                reason=value.get("reason"),
            )
        values[f.name] = value
    return cls(**values)


def _require(data: Mapping[str, Any], key: str) -> Any:
    if not isinstance(data, Mapping):
        raise ValueError(f"Expected an object with {key!r}")
    if key not in data:
        raise ValueError(f"Missing {key!r}")
    return data[key]


# Applying chunks


def apply_chunks(
    message: TranscriptMessage, chunks: Iterable[ResponseChunk]
) -> TranscriptMessage:
    """Return ``message`` with a response's chunks applied; ``message`` is kept.

    Port of upstream ``apply-chunks.ts`` ``applyChunks``. Parts still open at
    the end keep what they have. Deltas for parts that aren't open, and
    results for unknown tool calls, are dropped.
    """
    parts: list[MessagePart] = [dataclasses.replace(part) for part in message.parts]
    metadata = message.metadata
    open_parts: dict[tuple[str, str], TextPart | ReasoningPart] = {}

    for chunk in chunks:
        match chunk:
            case TextStart() | ReasoningStart():
                cls = TextPart if isinstance(chunk, TextStart) else ReasoningPart
                part = cls(text="", provider_metadata=chunk.provider_metadata)
                open_parts[cls.type, chunk.id] = part
                parts.append(part)
            case TextDelta() | ReasoningDelta():
                kind = "text" if isinstance(chunk, TextDelta) else "reasoning"
                if (part := open_parts.get((kind, chunk.id))) is not None:
                    part.text += chunk.delta
            case TextEnd() | ReasoningEnd():
                kind = "text" if isinstance(chunk, TextEnd) else "reasoning"
                part = open_parts.pop((kind, chunk.id), None)
                if part is not None and chunk.provider_metadata is not None:
                    part.provider_metadata = {
                        **(part.provider_metadata or {}),
                        **chunk.provider_metadata,
                    }
            case ToolInputStart() | ToolInputAvailable() | ToolInputError():
                _put_tool_call(parts, chunk)
            case ToolInputDelta() | Data(transient=True):
                pass
            case (
                ToolApprovalRequest()
                | ToolOutputAvailable()
                | ToolOutputError()
                | ToolOutputDenied()
            ):
                _put_tool_result(parts, chunk)
            case Data():
                data = DataPart(name=chunk.name, id=chunk.id, data=chunk.data)
                _put_data(parts, data)
            case Metadata():
                metadata = {**(metadata or {}), **chunk.metadata}
            case FilePart() | SourceUrlPart() | SourceDocumentPart():
                parts.append(dataclasses.replace(chunk))

    return TranscriptMessage(
        id=message.id, role=message.role, parts=parts, metadata=metadata
    )


_CALL_STATES: dict[type[Any], ToolState] = {
    ToolInputStart: "input-streaming",
    ToolInputAvailable: "input-available",
    ToolInputError: "output-error",
}


def _tool_part(parts: Sequence[MessagePart], tool_call_id: str) -> ToolPart | None:
    return next(
        (
            part
            for part in parts
            if isinstance(part, ToolPart) and part.tool_call_id == tool_call_id
        ),
        None,
    )


def _put_tool_call(
    parts: list[MessagePart],
    chunk: ToolInputStart | ToolInputAvailable | ToolInputError,
) -> None:
    # Upstream spreads the chunk over the existing part: only the fields the
    # chunk carries replace the part's.
    existing = _tool_part(parts, chunk.tool_call_id)
    fields = {} if existing is None else _present(existing)
    fields.update(_present(chunk))
    fields["state"] = _CALL_STATES[type(chunk)]
    part = ToolPart(**fields)

    if existing is None:
        parts.append(part)
    else:
        parts[parts.index(existing)] = part


def _present(record: _Record) -> dict[str, Any]:
    return {
        f.name: value
        for f in _fields(type(record))
        if (value := getattr(record, f.name)) is not None or f.required
    }


def _put_tool_result(
    parts: Sequence[MessagePart],
    chunk: (
        ToolApprovalRequest | ToolOutputAvailable | ToolOutputError | ToolOutputDenied
    ),
) -> None:
    part = _tool_part(parts, chunk.tool_call_id)
    if part is None:
        return

    match chunk:
        case ToolApprovalRequest():
            part.state = "approval-requested"
            part.approval = ToolApproval(id=chunk.approval_id)
        case ToolOutputAvailable():
            part.state = "output-available"
            part.output = chunk.output
            part.preliminary = chunk.preliminary
        case ToolOutputError():
            part.state = "output-error"
            part.error_text = chunk.error_text
        case ToolOutputDenied():
            part.state = "output-denied"


def _put_data(parts: list[MessagePart], data: DataPart) -> None:
    for index, part in enumerate(parts):
        if (
            isinstance(part, DataPart)
            and part.name == data.name
            and part.id is not None
            and part.id == data.id
        ):
            parts[index] = data
            return
    parts.append(data)


# Grammar

type _Step = Literal["start", "delta", "end", "close"]
"""``close`` ends a part that may never have started."""


class ResponseGrammar:
    """Checks a response's chunks, in order, against upstream's grammar.

    Port of the check in upstream ``response.ts``: a delta or end needs its
    part open, a start needs its id free within its kind (text and reasoning
    ids are separate), and ``tool-input-available`` / ``-error`` close a tool
    input that may never have opened.
    """

    __slots__ = ("_open",)

    def __init__(self) -> None:
        self._open: set[tuple[str, str]] = set()

    def check(self, chunk: ResponseChunk) -> None:
        """Raise if ``chunk`` can't come next; records nothing.

        Raises
        ------
        ResponseGrammarError
            If the chunk breaks the grammar.
        """
        step = _step(chunk)
        if step is None:
            return

        kind, id, action = step
        if action == "start" and (kind, id) in self._open:
            raise ResponseGrammarError(f'{kind} "{id}" is already open')
        if action in ("delta", "end") and (kind, id) not in self._open:
            raise ResponseGrammarError(f'{kind} "{id}" is not open')

    def append(self, chunk: ResponseChunk) -> None:
        """Check ``chunk``, then record the part it opens or closes.

        Raises
        ------
        ResponseGrammarError
            If the chunk breaks the grammar; nothing is recorded.
        """
        self.check(chunk)
        step = _step(chunk)
        if step is None:
            return

        kind, id, action = step
        if action == "start":
            self._open.add((kind, id))
        elif action != "delta":
            self._open.discard((kind, id))


def _step(chunk: ResponseChunk) -> tuple[str, str, _Step] | None:
    match chunk:
        case TextStart(id=id):
            return "text", id, "start"
        case TextDelta(id=id):
            return "text", id, "delta"
        case TextEnd(id=id):
            return "text", id, "end"
        case ReasoningStart(id=id):
            return "reasoning", id, "start"
        case ReasoningDelta(id=id):
            return "reasoning", id, "delta"
        case ReasoningEnd(id=id):
            return "reasoning", id, "end"
        case ToolInputStart(tool_call_id=id):
            return "tool input", id, "start"
        case ToolInputDelta(tool_call_id=id):
            return "tool input", id, "delta"
        case ToolInputAvailable(tool_call_id=id) | ToolInputError(tool_call_id=id):
            return "tool input", id, "close"
        case _:
            return None
