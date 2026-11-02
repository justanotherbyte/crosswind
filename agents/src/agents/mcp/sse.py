"""Streamable HTTP servers may answer a POST with ``text/event-stream``.

The GET listener is one long event stream, in the WHATWG format
(``.design/mcp_engine.md`` §4.4).
"""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator

from ..core import sse
from ..core.sse import MAX_EVENT_SIZE, SseEvent
from .errors import MCPProtocolError

__all__ = ("MAX_EVENT_SIZE", "SseEvent", "iter_events")


async def iter_events(chunks: AsyncIterable[bytes]) -> AsyncIterator[SseEvent]:
    """Yield the events in a byte stream, as `agents.core.sse.iter_events` does.

    Raises
    ------
    MCPProtocolError
        If one event's data is larger than `MAX_EVENT_SIZE`.
    """
    try:
        async for event in sse.iter_events(chunks):
            yield event
    except sse.EventTooLargeError as error:
        raise MCPProtocolError(str(error)) from None
