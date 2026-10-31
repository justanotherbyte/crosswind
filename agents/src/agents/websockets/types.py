from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, TypedDict

from ..state.types import StateSource

if TYPE_CHECKING:
    from workers import Request

    from .connection import Connection

__all__ = (
    "Attachment",
    "CallableMetadata",
    "ConnectionContext",
    "ConnectionDecision",
    "ConnectionMeta",
    "ConnectionTagger",
    "RpcRequest",
    "SyncedState",
    "WebSocketHandlers",
)


@dataclass(slots=True, kw_only=True, frozen=True)
class CallableMetadata:
    """What ``@callable`` records about a method.

    Parameters
    ----------
    description
        What the method does.
    streaming
        Whether the method streams its result (a `StreamingResponse` first
        argument, or an async generator).
    """

    description: str | None = None
    streaming: bool = False


# Wire protocol §5.4.
class RpcRequest(TypedDict):
    type: str
    id: str
    method: str
    args: list[Any]


@dataclass(slots=True, kw_only=True, frozen=True)
class ConnectionContext:
    """What a connection was opened with."""

    request: "Request"
    """The WebSocket upgrade request."""


@dataclass(slots=True, kw_only=True)
class WebSocketHandlers:
    """Connection hooks; each runs in the host context with the connection.

    Parameters
    ----------
    on_connect
        A connection was accepted (after the protocol frames, if any).
    on_message
        A frame the SDK didn't consume arrived. For handlers added with
        ``WebSockets.use``, returning ``True`` claims it, so later handlers
        don't see it.
    on_close
        The client closed the connection.
    on_error
        The connection failed.
    """

    on_connect: "Callable[[Connection, ConnectionContext], Awaitable[None]] | None" = (
        None
    )
    on_message: "Callable[[Connection, str | bytes], Awaitable[bool | None]] | None" = (
        None
    )
    on_close: "Callable[[Connection, int, str, bool], Awaitable[None]] | None" = None
    on_error: "Callable[[Connection, BaseException], Awaitable[None]] | None" = None


type ConnectionDecision = Callable[[Connection, ConnectionContext], Awaitable[bool]]

type ConnectionTagger = Callable[[Connection, ConnectionContext], Awaitable[list[str]]]


class SyncedState(Protocol):
    def get(self) -> Any: ...

    # Raises to reject the change.
    def set(self, state: Any, source: StateSource) -> None: ...


class ConnectionMeta(TypedDict):
    id: str
    tags: list[str]
    uri: str | None


# The runtime caps a socket's hibernation attachment at 16 KiB.
Attachment = TypedDict(
    "Attachment",
    {"__pk": ConnectionMeta, "__user": Any, "__flags": dict[str, Any]},
    total=False,
)
