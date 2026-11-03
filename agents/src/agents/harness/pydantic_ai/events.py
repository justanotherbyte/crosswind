"""Mapping Pydantic AI's stream events to response chunks."""

from __future__ import annotations

from pydantic_ai import (
    AgentStreamEvent,
    DeferredToolRequestsEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelResponsePart,
    ModelResponsePartDelta,
    NativeToolCallPart,
    NativeToolReturnPart,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPartDelta,
)
from pydantic_ai import FilePart as ModelFilePart
from pydantic_ai import TextPart as ModelTextPart
from pydantic_ai import TextPartDelta as ModelTextPartDelta
from pydantic_ai.messages import BaseToolCallPart, BaseToolReturnPart
from pydantic_core import to_jsonable_python

from ..transcript import (
    Data,
    DataPart,
    ReasoningDelta,
    ReasoningEnd,
    ReasoningStart,
    ResponseChunk,
    TextDelta,
    TextEnd,
    TextStart,
    ToolApprovalRequest,
    ToolInputAvailable,
    ToolInputDelta,
    ToolInputError,
    ToolInputStart,
    ToolOutputAvailable,
    ToolOutputDenied,
    ToolOutputError,
)
from .utils import INVALID_INPUT, file_part, invalid_args

__all__ = ("PartIds", "to_chunks")

_INVALID_ARGS = "The tool input didn't validate."


class PartIds:
    """Chunk part ids for one run: ``<node index>:<part index>``.

    Also remembers each streaming tool call's id by part index, since a
    tool call delta may not carry it.
    """

    __slots__ = ("_calls", "_node")

    def __init__(self) -> None:
        self._node = -1
        self._calls: dict[int, str] = {}

    def next_node(self) -> None:
        """Start the parts of the next model response."""
        self._node += 1
        self._calls.clear()

    def part(self, index: int) -> str:
        """Return the id of the current response's part ``index``."""
        return f"{self._node}:{index}"

    def start_call(self, index: int, tool_call_id: str) -> None:
        """Remember that part ``index`` is this tool call."""
        self._calls[index] = tool_call_id

    def call(self, index: int) -> str | None:
        """Return the tool call id of part ``index``, if it's a tool call."""
        return self._calls.get(index)


def to_chunks(event: AgentStreamEvent, ids: PartIds) -> list[ResponseChunk]:
    """Return the response chunks for ``event`` (none for most events)."""
    match event:
        case PartStartEvent(index=index, part=part):
            return _start(ids, index, part)
        case PartDeltaEvent(index=index, delta=delta):
            return _delta(ids, index, delta)
        case PartEndEvent(index=index, part=part):
            return _end(ids, index, part)
        case FunctionToolCallEvent(part=call, args_valid=False):
            return [
                ToolInputError(
                    tool_call_id=call.tool_call_id,
                    tool_name=call.tool_name,
                    input=call.args_as_dict(),
                    error_text=_INVALID_ARGS,
                )
            ]
        case FunctionToolResultEvent(part=result):
            return [_result(result)]
        case DeferredToolRequestsEvent(requests=requests):
            return [
                ToolApprovalRequest(
                    tool_call_id=call.tool_call_id, approval_id=call.tool_call_id
                )
                for call in requests.approvals
            ]
        case _:
            return []


def _start(ids: PartIds, index: int, part: ModelResponsePart) -> list[ResponseChunk]:
    id = ids.part(index)
    match part:
        case ModelTextPart(content=text):
            return [TextStart(id=id), *([TextDelta(id=id, delta=text)] if text else [])]
        case ThinkingPart(content=text):
            return [
                ReasoningStart(id=id),
                *([ReasoningDelta(id=id, delta=text)] if text else []),
            ]
        case BaseToolCallPart(args=args):
            ids.start_call(index, part.tool_call_id)
            start = ToolInputStart(
                tool_call_id=part.tool_call_id,
                tool_name=part.tool_name,
                provider_executed=_native(part),
            )
            if isinstance(args, str) and args:
                return [
                    start,
                    ToolInputDelta(tool_call_id=part.tool_call_id, delta=args),
                ]
            return [start]
        case NativeToolReturnPart():
            return [_result(part)]
        case ModelFilePart(content=content):
            kept = file_part(content)
            if isinstance(kept, DataPart):
                return [Data(name=kept.name, data=kept.data)]
            return [kept]
        case _:
            return []


def _delta(
    ids: PartIds, index: int, delta: ModelResponsePartDelta
) -> list[ResponseChunk]:
    id = ids.part(index)
    match delta:
        case ModelTextPartDelta(content_delta=text) if text:
            return [TextDelta(id=id, delta=text)]
        case ThinkingPartDelta(content_delta=text) if text:
            return [ReasoningDelta(id=id, delta=text)]
        case ToolCallPartDelta(args_delta=str(text)) if text and ids.call(index):
            # Dict deltas aren't text, so they show only once the input is whole.
            return [ToolInputDelta(tool_call_id=ids.call(index) or "", delta=text)]
        case _:
            return []


def _end(ids: PartIds, index: int, part: ModelResponsePart) -> list[ResponseChunk]:
    id = ids.part(index)
    match part:
        case ModelTextPart():
            return [TextEnd(id=id)]
        case ThinkingPart():
            return [ReasoningEnd(id=id)]
        case BaseToolCallPart():
            raw = invalid_args(part)
            if raw is not None:
                return [
                    ToolInputError(
                        tool_call_id=part.tool_call_id,
                        tool_name=part.tool_name,
                        input=raw,
                        error_text=INVALID_INPUT,
                        provider_executed=_native(part),
                    )
                ]
            return [
                ToolInputAvailable(
                    tool_call_id=part.tool_call_id,
                    tool_name=part.tool_name,
                    input=part.args_as_dict(),
                    provider_executed=_native(part),
                )
            ]
        case _:
            return []


def _native(part: BaseToolCallPart) -> bool | None:
    return True if isinstance(part, NativeToolCallPart) else None


def _result(part: BaseToolReturnPart) -> ResponseChunk:
    native = True if isinstance(part, NativeToolReturnPart) else None
    match part.outcome:
        case "success":
            return ToolOutputAvailable(
                tool_call_id=part.tool_call_id,
                output=to_jsonable_python(part.content),
                provider_executed=native,
            )
        case "denied":
            return ToolOutputDenied(tool_call_id=part.tool_call_id)
        case _:
            return ToolOutputError(
                tool_call_id=part.tool_call_id,
                error_text=part.model_response_str(wrap_if_error=False),
                provider_executed=native,
            )
