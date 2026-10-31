"""Upstream ``chat/stream-accumulator.ts``."""

from dataclasses import dataclass
from typing import Any, Literal

from ._json import dumps
from .builder import BuilderScratch, apply_chunk_to_parts

__all__ = ("ChunkAction", "ChunkResult", "StreamAccumulator")

type Part = dict[str, Any]
type Chunk = dict[str, Any]
type Message = dict[str, Any]


@dataclass(slots=True, kw_only=True, frozen=True)
class ChunkAction:
    type: Literal[
        "start",
        "finish",
        "message-metadata",
        "tool-approval-request",
        "cross-message-tool-update",
        "error",
    ]
    message_id: str | None = None
    metadata: dict[str, Any] | None = None
    finish_reason: str | None = None
    tool_call_id: str | None = None
    update_type: Literal["output-available", "output-error"] | None = None
    output: Any = None
    error_text: str | None = None
    preliminary: bool | None = None
    error: str | None = None


@dataclass(slots=True, kw_only=True, frozen=True)
class ChunkResult:
    handled: bool
    action: ChunkAction | None = None


class StreamAccumulator:
    __slots__ = (
        "_continuation",
        "_pending",
        "_scratch",
        "message_id",
        "metadata",
        "parts",
    )

    def __init__(
        self,
        *,
        message_id: str,
        continuation: bool = False,
        existing_parts: list[Part] | None = None,
        existing_metadata: dict[str, Any] | None = None,
    ) -> None:
        self.message_id = message_id
        self.parts: list[Part] = list(existing_parts) if existing_parts else []
        self.metadata: dict[str, Any] | None = (
            dict(existing_metadata) if existing_metadata is not None else None
        )
        self._continuation = continuation
        self._scratch = BuilderScratch()
        # Chunks seen before the continued message is known.
        self._pending: list[Chunk] | None = (
            []
            if continuation and existing_parts is None and existing_metadata is None
            else None
        )

    def apply_chunk(self, chunk: Chunk) -> ChunkResult:
        if self._pending is not None:
            self._pending.append(chunk)
        handled = apply_chunk_to_parts(self.parts, chunk, self._scratch)
        kind = chunk.get("type")
        tool_call_id = chunk.get("toolCallId")
        if kind == "tool-approval-request" and tool_call_id:
            return ChunkResult(
                handled=handled,
                action=ChunkAction(
                    type="tool-approval-request", tool_call_id=tool_call_id
                ),
            )
        if (
            kind in ("tool-output-available", "tool-output-error")
            and tool_call_id
            and not any(p.get("toolCallId") == tool_call_id for p in self.parts)
        ):
            return ChunkResult(
                handled=handled,
                action=ChunkAction(
                    type="cross-message-tool-update",
                    update_type=(
                        "output-available"
                        if kind == "tool-output-available"
                        else "output-error"
                    ),
                    tool_call_id=tool_call_id,
                    output=chunk.get("output"),
                    error_text=chunk.get("errorText"),
                    preliminary=chunk.get("preliminary"),
                ),
            )
        if handled:
            return ChunkResult(handled=True)
        match kind:
            case "start":
                if chunk.get("messageId") is not None and not self._continuation:
                    self.message_id = chunk["messageId"]
                metadata = _as_metadata(chunk.get("messageMetadata"))
                self._merge_metadata(metadata)
                return ChunkResult(
                    handled=True,
                    action=ChunkAction(
                        type="start",
                        message_id=chunk.get("messageId"),
                        metadata=metadata,
                    ),
                )
            case "finish":
                metadata = _as_metadata(chunk.get("messageMetadata"))
                self._merge_metadata(metadata)
                return ChunkResult(
                    handled=True,
                    action=ChunkAction(
                        type="finish",
                        finish_reason=chunk.get("finishReason"),
                        metadata=metadata,
                    ),
                )
            case "message-metadata":
                metadata = _as_metadata(chunk.get("messageMetadata"))
                self._merge_metadata(metadata)
                return ChunkResult(
                    handled=True,
                    action=ChunkAction(
                        type="message-metadata", metadata=metadata or {}
                    ),
                )
            case "finish-step":
                return ChunkResult(handled=True)
            case "error":
                error = chunk.get("errorText")
                if error is None:
                    error = dumps(chunk)
                return ChunkResult(
                    handled=True, action=ChunkAction(type="error", error=error)
                )
        return ChunkResult(handled=False)

    def to_message(self) -> Message:
        message: Message = {
            "id": self.message_id,
            "role": "assistant",
            "parts": list(self.parts),
        }
        if self.metadata is not None:
            message["metadata"] = self.metadata
        return message

    def merge_into(self, messages: list[Message]) -> list[Message]:
        index = next(
            (i for i, m in enumerate(messages) if m.get("id") == self.message_id), -1
        )
        if index < 0 and self._continuation:
            index = next(
                (
                    i
                    for i in range(len(messages) - 1, -1, -1)
                    if messages[i].get("role") == "assistant"
                ),
                -1,
            )

        if self._pending is not None:
            pending, self._pending = self._pending, None
            self.parts[:] = list(messages[index]["parts"]) if index >= 0 else []
            self._scratch = BuilderScratch()
            current = (
                _as_metadata(messages[index].get("metadata")) if index >= 0 else None
            )
            self.metadata = dict(current) if current is not None else None
            if index >= 0:
                self.message_id = messages[index]["id"]
            for chunk in pending:
                self.apply_chunk(chunk)

        message_id = messages[index]["id"] if index >= 0 else self.message_id
        partial = {**self.to_message(), "id": message_id}
        if index >= 0:
            updated = list(messages)
            updated[index] = partial
            return updated
        return [*messages, partial]

    def _merge_metadata(self, metadata: dict[str, Any] | None) -> None:
        if metadata is not None:
            self.metadata = (
                {**self.metadata, **metadata} if self.metadata else dict(metadata)
            )


def _as_metadata(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None
