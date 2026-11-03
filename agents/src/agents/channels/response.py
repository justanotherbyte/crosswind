"""Responses: one streamed, replayable run of output within a turn.

Port of upstream ``experimental/channels/response.ts``. A response has no
start or finish chunk: it begins when it is opened and ends when its producer
closes it. Every chunk is checked against `ResponseGrammar` before it is
recorded.
"""

from collections.abc import AsyncIterable

from ..harness.transcript import ResponseChunk, ResponseGrammar, to_wire
from ..streams import StreamWriter

__all__ = ("ResponseWriter",)


class ResponseWriter:
    """The producer's handle on one response, over its stream.

    Parameters
    ----------
    writer
        The response's stream, from ``Streams.open``.
    """

    __slots__ = ("_grammar", "_writer")

    def __init__(self, writer: StreamWriter) -> None:
        self._writer = writer
        self._grammar = ResponseGrammar()

    @property
    def id(self) -> str:
        """The response's id, which is its stream's id."""
        return self._writer.stream_id

    def append(self, chunk: ResponseChunk) -> None:
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
        self._grammar.check(chunk)
        self._writer.append(to_wire(chunk))
        self._grammar.append(chunk)

    def end(self) -> None:
        """End the response. Parts still open are closed."""
        self._writer.close()

    async def pipe(self, chunks: AsyncIterable[ResponseChunk]) -> None:
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
