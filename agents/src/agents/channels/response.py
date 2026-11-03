"""Responses: one streamed, replayable run of output within a turn.

Port of upstream ``experimental/channels/response.ts``. A response has no
start or finish chunk: it begins when it is opened and ends when its producer
closes it. The grammar, checked on append: a delta or end needs its part open;
a start needs its id free within its kind (text and reasoning ids are
separate); ``tool-input-available`` and ``tool-input-error`` close a tool
input that may never have opened.
"""

from collections.abc import AsyncIterable, Callable
from typing import Literal

from ..streams import StreamWriter
from .errors import ResponseGrammarError
from .protocol import WireChunk

__all__ = ("ResponseWriter",)

# "close" ends a part that may never have been started.
type _Step = Literal["start", "delta", "end", "close"]

_PARTS: dict[str, tuple[str, str, _Step]] = {
    "text-start": ("text", "id", "start"),
    "text-delta": ("text", "id", "delta"),
    "text-end": ("text", "id", "end"),
    "reasoning-start": ("reasoning", "id", "start"),
    "reasoning-delta": ("reasoning", "id", "delta"),
    "reasoning-end": ("reasoning", "id", "end"),
    "tool-input-start": ("tool input", "toolCallId", "start"),
    "tool-input-delta": ("tool input", "toolCallId", "delta"),
    "tool-input-available": ("tool input", "toolCallId", "close"),
    "tool-input-error": ("tool input", "toolCallId", "close"),
}


class ResponseWriter:
    """The producer's handle on one response, over its stream.

    Parameters
    ----------
    writer
        The response's stream, from ``Streams.open``.
    """

    __slots__ = ("_open", "_writer")

    def __init__(self, writer: StreamWriter) -> None:
        self._writer = writer
        # Open parts, keyed by kind and id.
        self._open: set[tuple[str, str]] = set()

    @property
    def id(self) -> str:
        """The response's id, which is its stream's id."""
        return self._writer.stream_id

    def append(self, chunk: WireChunk) -> None:
        """Record ``chunk`` and wake readers.

        Raises
        ------
        ResponseGrammarError
            If the chunk breaks the grammar. Nothing is recorded.
        StreamClosedError
            If the response has ended.
        StreamSerializationError
            If the chunk isn't JSON or is over the stream's size limit.
        """
        apply = self._check(chunk)
        self._writer.append(chunk)
        if apply is not None:
            apply()

    def end(self) -> None:
        """End the response. Parts still open are closed."""
        self._writer.close()

    async def pipe(self, chunks: AsyncIterable[WireChunk]) -> None:
        """Append every chunk, then end the response.

        If the source or an append fails, or the task is cancelled, the
        response settles as interrupted and the error propagates.
        """
        try:
            async for chunk in chunks:
                self.append(chunk)
        except BaseException:
            self._writer.error("interrupted")
            raise
        self.end()

    def _check(self, chunk: WireChunk) -> Callable[[], None] | None:
        # Returns the change the chunk makes to the open parts, applied only
        # once the chunk is recorded.
        part = _PARTS.get(str(chunk.get("type")))
        if part is None:
            return None
        kind, id_field, step = part
        part_id = str(chunk.get(id_field))
        key = (kind, part_id)
        if step == "start":
            if key in self._open:
                raise ResponseGrammarError(f'{kind} "{part_id}" is already open')
            return lambda: self._open.add(key)
        if step != "close" and key not in self._open:
            raise ResponseGrammarError(f'{kind} "{part_id}" is not open')
        if step == "delta":
            return None
        return lambda: self._open.discard(key)
