"""Mapping Pydantic AI's stream events to harness events."""

from __future__ import annotations

from pydantic_ai import (
    AgentStreamEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
)
from pydantic_ai.messages import ERROR_OUTCOMES
from pydantic_core import to_jsonable_python

from ..types import HarnessEvent, TextEvent, ToolCallEvent, ToolResultEvent

__all__ = ("to_harness_event",)


def to_harness_event(operation_id: str, event: AgentStreamEvent) -> HarnessEvent | None:
    """Return the harness event for ``event``, or ``None`` if it has none."""
    match event:
        case (
            PartStartEvent(part=TextPart(content=delta))
            | PartDeltaEvent(delta=TextPartDelta(content_delta=delta))
        ) if delta:
            return TextEvent(operation_id=operation_id, delta=delta)
        case FunctionToolCallEvent(part=call):
            return ToolCallEvent(
                operation_id=operation_id,
                tool_call_id=call.tool_call_id,
                name=call.tool_name,
                args=call.args_as_dict(),
            )
        case FunctionToolResultEvent(part=result):
            return ToolResultEvent(
                operation_id=operation_id,
                tool_call_id=result.tool_call_id,
                result=to_jsonable_python(result.content),
                is_error=result.outcome in ERROR_OUTCOMES,
            )
        case _:
            return None
