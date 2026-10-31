"""Streamable HTTP servers may answer a POST with ``text/event-stream``.

The GET listener is one long event stream, in the WHATWG format
(``.design/mcp_engine.md`` §4.4).
"""

import codecs
import re
from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import dataclass

from .errors import MCPProtocolError

__all__ = ("MAX_EVENT_SIZE", "SseEvent", "iter_events")

MAX_EVENT_SIZE = 1024 * 1024  # characters, as the SDK's 1 MiB limit

_LINE_END = re.compile(r"\r\n|\r|\n")


@dataclass(slots=True, kw_only=True)
class SseEvent:
    event: str = "message"
    data: str = ""
    id: str | None = None
    retry_ms: int | None = None


class _Decoder:
    __slots__ = ("_data", "_event", "_id", "_retry_ms", "_size")

    def __init__(self) -> None:
        self._id: str | None = None
        self._retry_ms: int | None = None
        self._reset()

    def _reset(self) -> None:
        self._event = ""
        self._data: list[str] = []
        self._size = 0

    def feed(self, line: str) -> SseEvent | None:
        if line == "":
            return self._dispatch()
        if line.startswith(":"):
            return None
        name, colon, value = line.partition(":")
        if colon and value.startswith(" "):
            value = value[1:]

        if name == "data":
            self._size += len(value) + 1
            if self._size > MAX_EVENT_SIZE:
                raise MCPProtocolError("SSE event is larger than 1 MiB")
            self._data.append(value)
        elif name == "event":
            self._event = value
        elif name == "id" and "\0" not in value:
            self._id = value
        elif name == "retry" and value.isascii() and value.isdigit():
            self._retry_ms = int(value)
        return None

    def _dispatch(self) -> SseEvent | None:
        # Only an event with at least one data line is dispatched; a priming
        # event is "data:" with nothing after it.
        if not self._data:
            self._reset()
            return None
        event = SseEvent(
            event=self._event or "message",
            data="\n".join(self._data),
            id=self._id,
            retry_ms=self._retry_ms,
        )
        self._reset()
        return event


# The format drops an event that the end of the stream cuts off.
async def iter_events(chunks: AsyncIterable[bytes]) -> AsyncIterator[SseEvent]:
    decoder = _Decoder()
    text = codecs.getincrementaldecoder("utf-8")(errors="replace")
    buffer = ""
    first = True
    async for chunk in chunks:
        buffer += text.decode(chunk)
        if first and buffer:
            buffer = buffer.removeprefix("﻿")
            first = False
        lines, buffer = _split_lines(buffer)
        for line in lines:
            event = decoder.feed(line)
            if event is not None:
                yield event
        if len(buffer) > MAX_EVENT_SIZE:
            raise MCPProtocolError("SSE line is larger than 1 MiB")

    # A CR held back for a CRLF that never came still ends its line.
    if buffer.endswith("\r"):
        event = decoder.feed(buffer[:-1])
        if event is not None:
            yield event


def _split_lines(buffer: str) -> tuple[list[str], str]:
    # A trailing CR may be the first half of a CRLF split across chunks, so it
    # stays in the buffer until the next character arrives.
    held = buffer.endswith("\r")
    *lines, rest = _LINE_END.split(buffer.removesuffix("\r"))
    return lines, (rest + "\r") if held else rest
