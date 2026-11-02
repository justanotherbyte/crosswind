"""Port of upstream ``lifecycle/capability-runner.ts``."""

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from .capability import LifecycleCapability, implements
from .types import MemoryLimitContext, RouteContext, WebSocket

if TYPE_CHECKING:
    from workers import Request, Response

__all__ = ("CapabilityRunner",)

_log = logging.getLogger("agents.lifecycle")


class CapabilityRunner:
    __slots__ = ("_capabilities", "_started")

    def __init__(self, capabilities: Sequence[LifecycleCapability]) -> None:
        # Lifecycle's list, in dispatch order with catch-alls last. Read live, so
        # capabilities installed before startup are included.
        self._capabilities = capabilities
        self._started = False

    def _implementing(self, hook: str) -> Sequence[LifecycleCapability]:
        return [cap for cap in self._capabilities if implements(cap, hook)]

    def _require_started(self, operation: str) -> None:
        if not self._started:
            raise RuntimeError(
                f"Cannot {operation} before the Durable Object lifecycle has started"
            )

    async def start(self) -> None:
        for capability in self._implementing("on_start"):
            await capability.on_start()
        self._started = True

    def reset(self) -> None:
        self._started = False

    async def request(self, request: "Request") -> "Response | None":
        self._require_started("handle a request")
        for capability in self._implementing("on_request"):
            response = await capability.on_request(request)
            if response is not None:
                return response
        return None

    async def websocket_upgrade(self, request: "Request") -> "Response | None":
        self._require_started("handle a WebSocket upgrade")
        for capability in self._implementing("on_websocket_upgrade"):
            response = await capability.on_websocket_upgrade(request)
            if response is not None:
                return response
        return None

    async def websocket_message(self, ws: WebSocket, message: str | bytes) -> bool:
        self._require_started("handle a WebSocket message")
        for capability in self._implementing("on_websocket_message"):
            if await capability.on_websocket_message(ws, message):
                return True
        return False

    async def websocket_close(
        self, ws: WebSocket, code: int, reason: str, was_clean: bool
    ) -> bool:
        self._require_started("handle a WebSocket close")
        for capability in self._implementing("on_websocket_close"):
            if await capability.on_websocket_close(ws, code, reason, was_clean):
                return True
        return False

    async def websocket_error(self, ws: WebSocket, error: BaseException) -> bool:
        self._require_started("handle a WebSocket error")
        for capability in self._implementing("on_websocket_error"):
            if await capability.on_websocket_error(ws, error):
                return True
        return False

    def find(self, capability_id: str) -> LifecycleCapability | None:
        self._require_started("dispatch capability work")
        return next(
            (c for c in self._capabilities if c.capability_id == capability_id), None
        )

    async def route(self, capability_id: str, context: RouteContext) -> Any:
        self._require_started("route a capability message")
        capability = self.find(capability_id)
        if capability is None or not implements(capability, "on_route"):
            raise RuntimeError(
                f"Lifecycle capability {capability_id!r} cannot receive routed messages"
            )
        return await capability.on_route(context)

    async def memory_limit(self, context: MemoryLimitContext) -> None:
        # Not gated on startup: the strike may have come from startup itself.
        # One capability's failure doesn't stop the next; the isolate is about
        # to reset either way.
        for capability in self._implementing("on_memory_limit"):
            try:
                await capability.on_memory_limit(context)
            except Exception:
                _log.exception(
                    "Capability %r memory-limit policy failed", capability.capability_id
                )

    async def dispose(self) -> None:
        for capability in reversed(self._implementing("dispose")):
            try:
                await capability.dispose()
            except Exception:
                _log.exception(
                    "Capability %r disposal failed", capability.capability_id
                )
