"""AI SDK v6 ``UIMessageChunk``s, per ``.design/chat_models.md`` §2.

Fields match ``ai@6.0.300`` in snake_case. `agents.chat.codec` writes the
camelCase wire form.
"""

from dataclasses import dataclass
from typing import ClassVar, Literal

from ..core.records import wire
from ..core.types import JSONValue

__all__ = (
    "Abort",
    "Data",
    "Error",
    "File",
    "Finish",
    "FinishReason",
    "FinishStep",
    "MessageMetadata",
    "ProviderMetadata",
    "ReasoningDelta",
    "ReasoningEnd",
    "ReasoningStart",
    "SourceDocument",
    "SourceUrl",
    "Start",
    "StartStep",
    "TextDelta",
    "TextEnd",
    "TextStart",
    "ToolApprovalRequest",
    "ToolInputAvailable",
    "ToolInputDelta",
    "ToolInputError",
    "ToolInputStart",
    "ToolOutputAvailable",
    "ToolOutputDenied",
    "ToolOutputError",
    "UIMessageChunk",
)

type ProviderMetadata = dict[str, dict[str, JSONValue]]

type FinishReason = Literal[
    "stop", "length", "content-filter", "tool-calls", "error", "other"
]


@dataclass(slots=True, kw_only=True)
class TextStart:
    type: ClassVar[str] = "text-start"
    id: str
    provider_metadata: ProviderMetadata | None = wire("providerMetadata", None)


@dataclass(slots=True, kw_only=True)
class TextDelta:
    type: ClassVar[str] = "text-delta"
    id: str
    delta: str
    provider_metadata: ProviderMetadata | None = wire("providerMetadata", None)


@dataclass(slots=True, kw_only=True)
class TextEnd:
    type: ClassVar[str] = "text-end"
    id: str
    provider_metadata: ProviderMetadata | None = wire("providerMetadata", None)


@dataclass(slots=True, kw_only=True)
class ReasoningStart:
    type: ClassVar[str] = "reasoning-start"
    id: str
    provider_metadata: ProviderMetadata | None = wire("providerMetadata", None)


@dataclass(slots=True, kw_only=True)
class ReasoningDelta:
    type: ClassVar[str] = "reasoning-delta"
    id: str
    delta: str
    provider_metadata: ProviderMetadata | None = wire("providerMetadata", None)


@dataclass(slots=True, kw_only=True)
class ReasoningEnd:
    type: ClassVar[str] = "reasoning-end"
    id: str
    provider_metadata: ProviderMetadata | None = wire("providerMetadata", None)


@dataclass(slots=True, kw_only=True)
class ToolInputStart:
    type: ClassVar[str] = "tool-input-start"
    tool_call_id: str = wire("toolCallId")
    tool_name: str = wire("toolName")
    provider_executed: bool | None = wire("providerExecuted", None)
    provider_metadata: ProviderMetadata | None = wire("providerMetadata", None)
    tool_metadata: dict[str, JSONValue] | None = wire("toolMetadata", None)
    dynamic: bool | None = None
    title: str | None = None


@dataclass(slots=True, kw_only=True)
class ToolInputDelta:
    type: ClassVar[str] = "tool-input-delta"
    tool_call_id: str = wire("toolCallId")
    input_text_delta: str = wire("inputTextDelta")


@dataclass(slots=True, kw_only=True)
class ToolInputAvailable:
    type: ClassVar[str] = "tool-input-available"
    tool_call_id: str = wire("toolCallId")
    tool_name: str = wire("toolName")
    input: JSONValue
    provider_executed: bool | None = wire("providerExecuted", None)
    provider_metadata: ProviderMetadata | None = wire("providerMetadata", None)
    tool_metadata: dict[str, JSONValue] | None = wire("toolMetadata", None)
    dynamic: bool | None = None
    title: str | None = None


@dataclass(slots=True, kw_only=True)
class ToolInputError:
    type: ClassVar[str] = "tool-input-error"
    tool_call_id: str = wire("toolCallId")
    tool_name: str = wire("toolName")
    input: JSONValue
    error_text: str = wire("errorText")
    provider_executed: bool | None = wire("providerExecuted", None)
    provider_metadata: ProviderMetadata | None = wire("providerMetadata", None)
    tool_metadata: dict[str, JSONValue] | None = wire("toolMetadata", None)
    dynamic: bool | None = None
    title: str | None = None


@dataclass(slots=True, kw_only=True)
class ToolApprovalRequest:
    type: ClassVar[str] = "tool-approval-request"
    approval_id: str = wire("approvalId")
    tool_call_id: str = wire("toolCallId")
    approval_descriptor: JSONValue = wire("approvalDescriptor", None)
    input_schema_input: JSONValue = wire("inputSchemaInput", None)
    signature: str | None = None


@dataclass(slots=True, kw_only=True)
class ToolOutputAvailable:
    type: ClassVar[str] = "tool-output-available"
    tool_call_id: str = wire("toolCallId")
    output: JSONValue
    provider_executed: bool | None = wire("providerExecuted", None)
    provider_metadata: ProviderMetadata | None = wire("providerMetadata", None)
    tool_metadata: dict[str, JSONValue] | None = wire("toolMetadata", None)
    dynamic: bool | None = None
    preliminary: bool | None = None  # a later chunk may replace this output


@dataclass(slots=True, kw_only=True)
class ToolOutputError:
    type: ClassVar[str] = "tool-output-error"
    tool_call_id: str = wire("toolCallId")
    error_text: str = wire("errorText")
    provider_executed: bool | None = wire("providerExecuted", None)
    provider_metadata: ProviderMetadata | None = wire("providerMetadata", None)
    tool_metadata: dict[str, JSONValue] | None = wire("toolMetadata", None)
    dynamic: bool | None = None


@dataclass(slots=True, kw_only=True)
class ToolOutputDenied:
    type: ClassVar[str] = "tool-output-denied"
    tool_call_id: str = wire("toolCallId")


@dataclass(slots=True, kw_only=True)
class SourceUrl:
    type: ClassVar[str] = "source-url"
    source_id: str = wire("sourceId")
    url: str
    title: str | None = None
    provider_metadata: ProviderMetadata | None = wire("providerMetadata", None)


@dataclass(slots=True, kw_only=True)
class SourceDocument:
    type: ClassVar[str] = "source-document"
    source_id: str = wire("sourceId")
    media_type: str = wire("mediaType")
    title: str
    filename: str | None = None
    provider_metadata: ProviderMetadata | None = wire("providerMetadata", None)


@dataclass(slots=True, kw_only=True)
class File:
    type: ClassVar[str] = "file"
    url: str  # may be a ``data:`` URL
    media_type: str = wire("mediaType")
    provider_metadata: ProviderMetadata | None = wire("providerMetadata", None)


# Its wire type is ``data-{name}``. A later chunk with the same ``name`` and
# ``id`` replaces the part. ``transient`` data reaches clients but isn't saved.
@dataclass(slots=True, kw_only=True)
class Data:
    name: str
    data: JSONValue
    id: str | None = None
    transient: bool = False


# The part it makes is ``step-start``.
@dataclass(slots=True, kw_only=True)
class StartStep:
    type: ClassVar[str] = "start-step"


@dataclass(slots=True, kw_only=True)
class FinishStep:
    type: ClassVar[str] = "finish-step"


# The SDK sends ``Start`` and ``Finish`` itself if the stream doesn't.
@dataclass(slots=True, kw_only=True)
class Start:
    type: ClassVar[str] = "start"
    message_id: str | None = wire("messageId", None)
    message_metadata: JSONValue = wire("messageMetadata", None)


@dataclass(slots=True, kw_only=True)
class Finish:
    type: ClassVar[str] = "finish"
    finish_reason: FinishReason | None = wire("finishReason", None)
    message_metadata: JSONValue = wire("messageMetadata", None)


@dataclass(slots=True, kw_only=True)
class MessageMetadata:
    type: ClassVar[str] = "message-metadata"
    message_metadata: JSONValue = wire("messageMetadata")


@dataclass(slots=True, kw_only=True)
class Error:
    type: ClassVar[str] = "error"
    error_text: str = wire("errorText")


@dataclass(slots=True, kw_only=True)
class Abort:
    type: ClassVar[str] = "abort"
    reason: str | None = None


type UIMessageChunk = (
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
    | SourceUrl
    | SourceDocument
    | File
    | Data
    | StartStep
    | FinishStep
    | Start
    | Finish
    | MessageMetadata
    | Error
    | Abort
)
