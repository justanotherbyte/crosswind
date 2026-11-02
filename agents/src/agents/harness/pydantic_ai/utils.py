"""Helpers on Pydantic AI message histories."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic_ai import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)

__all__ = ("dump_messages", "load_messages", "pending_calls")


def load_messages(data: bytes | None) -> list[ModelMessage]:
    """Parse a stored history; ``None`` is an empty one."""
    if data is None:
        return []

    return ModelMessagesTypeAdapter.validate_json(data)


def dump_messages(messages: Sequence[ModelMessage]) -> bytes:
    """Serialize a history for storage."""
    return ModelMessagesTypeAdapter.dump_json(list(messages))


def pending_calls(messages: Sequence[ModelMessage]) -> list[ToolCallPart]:
    """Return the last response's tool calls that have no result yet."""
    answered: set[str] = set()
    for message in reversed(messages):
        if isinstance(message, ModelResponse):
            return [
                call for call in message.tool_calls if call.tool_call_id not in answered
            ]

        answered.update(
            part.tool_call_id
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        )
    return []
