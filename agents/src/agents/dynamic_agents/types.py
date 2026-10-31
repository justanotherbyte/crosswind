from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, NamedTuple, Protocol, TypedDict

if TYPE_CHECKING:
    from workers import Request, Response

    from ..lifecycle.lifecycle import Lifecycle
    from ..websockets.connection import Connection
    from ..websockets.websockets import WebSockets

__all__ = (
    "AgentPathStep",
    "AgentRoute",
    "ForwardedConnection",
    "SubAgentInfo",
    "SubAgentPathMatch",
    "SubAgentsHost",
    "WireEnvelope",
    "WireRouteAddress",
)


class AgentRoute(NamedTuple):
    """Which agent instance a request is for."""

    class_name: str
    """The class name: the env binding name from ``route_agent_request``,
    the sub-agent's class for ``on_before_sub_agent``."""
    name: str
    """The instance name."""


class AgentPathStep(NamedTuple):
    """One agent in a path from the root: its class name and instance name."""

    class_name: str
    name: str


@dataclass(slots=True, kw_only=True)
class SubAgentInfo:
    """A sub-agent a parent has created (from its registry)."""

    class_name: str
    name: str
    created_at: datetime


class SubAgentPathMatch(NamedTuple):
    child_class: str
    child_name: str
    remaining_path: str


class ForwardedConnection(TypedDict):
    id: str
    uri: str | None
    tags: list[str]
    state: Any
    flags: dict[str, Any]
    request_headers: list[list[str]] | None


class WireRouteAddress(TypedDict):
    key: str
    data: str


class WireEnvelope(TypedDict):
    capability: str
    source: WireRouteAddress | None
    payload: Any


class SubAgentsHost(Protocol):
    @property
    def ctx(self) -> Any: ...

    @property
    def lifecycle(self) -> "Lifecycle": ...

    @property
    def _websockets(self) -> "WebSockets": ...

    async def on_before_sub_agent(
        self, request: "Request", child: Any
    ) -> "Request | Response | None": ...

    def _connect_virtual(
        self, connection: "Connection", request: "Request"
    ) -> Awaitable[None]: ...

    def _message_locally(
        self, connection: "Connection", message: str | bytes
    ) -> Awaitable[None]: ...

    def _close_locally(
        self, connection: "Connection", code: int, reason: str, was_clean: bool
    ) -> Awaitable[None]: ...

    def _cleanup_route_prefix(self, prefix: str) -> Awaitable[None]: ...
