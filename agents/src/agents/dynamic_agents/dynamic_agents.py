"""Port of upstream ``dynamic-agents/dynamic-agents.ts``.

See ``.design/subagents_engine.md``. A facet runs in its own isolate with its
own SQLite on the root's machine, and has no alarm of its own. The root owns
the physical alarm and every native client socket of the tree.
"""

import asyncio
import json
import logging
from collections.abc import Callable, Iterator, Sequence
from contextlib import suppress
from contextvars import ContextVar
from dataclasses import dataclass
from functools import partial
from typing import Any, Final, Literal, override
from urllib.parse import urlsplit

from workers import Request, Response

from .. import _ffi
from ..core.naming import camel_case_to_kebab_case
from ..lifecycle.capability import LifecycleCapability
from ..lifecycle.host_context import run_in_host_context
from ..lifecycle.lifecycle import is_upgrade_request
from ..lifecycle.types import RouteAddress, RouteEnvelope
from ..websockets.connection import Connection
from ..websockets.websockets import _send
from .connections import (
    DELETED_FLAG,
    OUTER_URL_FLAG,
    TAGS_FLAG,
    VirtualConnection,
    apply_forwarded_state,
    forwarded_meta,
)
from .errors import (
    InvalidSubAgentNameError,
    SubAgentAbortedError,
    UnknownSubAgentClassError,
)
from .paths import (
    SUB_PREFIX,
    identity_name,
    is_same_prefix,
    parse_sub_agent_path,
    path_from_json,
    path_key,
    path_to_json,
    rewrite_pathname,
)
from .registry import SubAgentRegistry
from .types import (
    AgentPathStep,
    AgentRoute,
    ForwardedConnection,
    SubAgentPathMatch,
    SubAgentsHost,
    WireEnvelope,
    WireRouteAddress,
)

__all__ = ("OUTER_URL_HEADER", "SubAgentsEngine", "as_message", "rejection_close")

_log = logging.getLogger("agents.dynamic_agents")

OUTER_URL_HEADER = "x-cf-agents-subagent-url"

_FACET_KEY = "cf_agents_facet"
_DROPPED: Final = "dropped"


@dataclass(slots=True)
class _Frame:
    connection_id: str
    bridge: Any | None


# The bridge of the event currently being handled in this facet. Cleared when
# the event returns: a bridge is only valid while its event is in flight.
_frame: ContextVar[_Frame | None] = ContextVar("agents_sub_agent_frame", default=None)


def rejection_close(response: Response) -> tuple[int, str]:
    # Clients treat a 4000 + status close as final, and retry after a 1011.
    status = response.status
    code = 4000 + status if 400 <= status < 500 else 1011
    return code, f"Sub-agent connection rejected ({status})"


class _FacetTransport:
    __slots__ = ("_engine",)

    def __init__(self, engine: "SubAgentsEngine") -> None:
        self._engine = engine

    @property
    def source(self) -> RouteAddress | None:
        return self._engine.route_address()

    async def to_root(self, envelope: RouteEnvelope) -> Any:
        return await self._engine.route_to_root(envelope)

    async def to(self, target: RouteAddress, envelope: RouteEnvelope) -> Any:
        return await self._engine.route_to_target(target, envelope)


# Installed first: the other capabilities route through it, so a facet's
# identity must be restored before they start.
class SubAgentsEngine(LifecycleCapability):
    def __init__(self, host: SubAgentsHost) -> None:
        super().__init__("dynamic-agents")
        self._host = host
        self.is_facet = False
        self.facet_name: str | None = None
        self.parent_path: list[AgentPathStep] = []
        self._registry: SubAgentRegistry | None = None
        self.transport = _FacetTransport(self)
        self._virtual: dict[str, VirtualConnection] = {}
        self._tails: dict[str, asyncio.Task[None]] = {}
        self._background: set[asyncio.Task[Any]] = set()

    @property
    def registry(self) -> SubAgentRegistry:
        if self._registry is None:
            self._registry = SubAgentRegistry(self._host.lifecycle.sql)
        return self._registry

    @property
    def self_path(self) -> list[AgentPathStep]:
        own = AgentPathStep(
            class_name=type(self._host).__name__, name=self._host.lifecycle.name
        )
        return [*self.parent_path, own]

    def route_address(self) -> RouteAddress | None:
        if not self.is_facet:
            return None
        path = self.self_path
        return RouteAddress(key=path_key(path), data=path_to_json(path))

    @override
    async def on_start(self) -> None:
        stored = await self.lifecycle.storage.get(_FACET_KEY)
        if not isinstance(stored, str):
            return
        record = json.loads(stored)
        self._become_facet(record["name"], path_from_json(record["parent_path"]))
        # Not awaited, because the root may be the one starting this facet.
        self._spawn(self._load_root_connections())

    async def init_as_facet(
        self, name: str, parent_path_json: str, identity: str
    ) -> None:
        routed = getattr(self._host.ctx.id, "name", None)
        if routed != identity:
            raise RuntimeError(
                f"Facet bootstrap mismatch: expected identity {identity!r}, "
                f"got {routed!r}"
            )
        parent_path = path_from_json(parent_path_json)
        if not (
            self.is_facet
            and self.facet_name == name
            and self.parent_path == parent_path
        ):
            self._become_facet(name, parent_path)
            record = json.dumps({"name": name, "parent_path": parent_path_json})
            await self._host.ctx.storage.put(_FACET_KEY, record)
        # Native RPC bypasses fetch, where startup normally happens.
        await self._host.lifecycle.start()

    def _become_facet(self, name: str, parent_path: list[AgentPathStep]) -> None:
        self.is_facet = True
        self.facet_name = name
        self.parent_path = parent_path
        self._host.lifecycle._set_name(name)

    async def resolve(self, class_name: str, name: str) -> Any:
        if camel_case_to_kebab_case(class_name) == SUB_PREFIX:
            raise ValueError(
                f"Sub-agent class {class_name!r} kebab-cases to {SUB_PREFIX!r}, "
                "the reserved URL separator; rename it (e.g. SubThing)"
            )
        if class_name not in _ffi.export_names(self._host.ctx):
            raise UnknownSubAgentClassError(
                f"Sub-agent class {class_name!r} isn't exported from the Worker "
                "module under that name"
            )
        if "\0" in name:
            raise InvalidSubAgentNameError("Sub-agent names can't contain NUL (\\0)")
        child_path = [*self.self_path, AgentPathStep(class_name=class_name, name=name)]
        existing = self.registry.identity(class_name, name)
        identity = existing if existing is not None else identity_name(name, child_path)
        # Recorded before the handshake, so a child that's initialized isn't
        # left unregistered if this parent is interrupted afterwards.
        self.registry.record(class_name, name, identity)
        try:
            return await self._open_facet(class_name, name, identity)
        except Exception:
            if existing is None:
                self.registry.forget(class_name, name)
            raise

    async def _resolve_existing(self, class_name: str, name: str) -> Any | None:
        identity = self.registry.identity(class_name, name)
        if identity is None:
            return None
        try:
            return await self._open_facet(class_name, name, identity)
        except Exception:
            if not self.registry.has(class_name, name):
                return None  # deleted mid-handshake
            raise

    async def _open_facet(self, class_name: str, name: str, identity: str) -> Any:
        stub = _ffi.facet_get(
            self._host.ctx,
            _facet_key(class_name, name),
            class_name,
            self._root_class(),
            identity,
        )
        await _ffi.call_rpc(
            stub, "_cf_init_as_facet", name, path_to_json(self.self_path), identity
        )
        return stub

    def _root_class(self) -> str:
        root_class = (
            self.parent_path[0].class_name
            if self.is_facet
            else type(self._host).__name__
        )
        if not _ffi.has_namespace(self._host.ctx, root_class):
            raise RuntimeError(
                f"Sub-agents need the root agent class {root_class!r} exported "
                "and bound as a Durable Object namespace (its id namespace "
                "names every facet in the tree)"
            )
        return root_class

    def _root_stub(self) -> Any:
        root = self.parent_path[0]
        return _ffi.namespace_stub(self._host.ctx, root.class_name, root.name)

    def abort(self, class_name: str, name: str, reason: Exception | None) -> None:
        _ffi.facet_abort(
            self._host.ctx,
            _facet_key(class_name, name),
            reason if reason is not None else SubAgentAbortedError("Sub-agent aborted"),
        )

    async def delete(self, class_name: str, name: str) -> None:
        child_path = [*self.self_path, AgentPathStep(class_name=class_name, name=name)]
        await self._close_connections_for(child_path)
        _ffi.facet_delete(self._host.ctx, _facet_key(class_name, name))
        self.registry.forget(class_name, name)
        await self._cleanup_routed_work(child_path)

    async def destroy_self(self) -> None:
        await _ffi.call_rpc(
            self._root_stub(),
            "_cf_destroy_descendant_facet",
            path_to_json(self.self_path),
        )

    async def destroy_descendant(self, target: list[AgentPathStep]) -> None:
        own = self.self_path
        if len(target) <= len(own) or not is_same_prefix(own, target):
            raise ValueError("The facet to destroy must be a strict descendant")
        if not self.is_facet:
            await self.cleanup_prefix(target)  # the root owns all routed work
        step = target[len(own)]
        if len(target) == len(own) + 1:
            await self._close_connections_for(target)
            _ffi.facet_delete(self._host.ctx, _facet_key(step.class_name, step.name))
            self.registry.forget(step.class_name, step.name)
            return
        if not self.registry.has(step.class_name, step.name):
            return
        child = await self.resolve(step.class_name, step.name)
        await _ffi.call_rpc(child, "_cf_destroy_descendant_facet", path_to_json(target))

    # Root only. A facet subtree's schedules, queue items, task wakes and
    # fiber index entries live on the root.
    async def cleanup_prefix(self, path: Sequence[AgentPathStep]) -> None:
        # The capabilities' tables exist once started (upstream initializes
        # first too).
        await self._host.lifecycle.start()
        await self._host._cleanup_route_prefix(path_key(path))

    async def _cleanup_routed_work(self, path: list[AgentPathStep]) -> None:
        if self.is_facet:
            await _ffi.call_rpc(
                self._root_stub(), "_cf_cleanup_facet_prefix", path_to_json(path)
            )
        else:
            await self.cleanup_prefix(path)

    async def route_to_root(self, envelope: RouteEnvelope) -> Any:
        if not self.is_facet:
            return await self._host.lifecycle.route(envelope)
        return await _ffi.call_rpc(
            self._root_stub(), "_cf_route_lifecycle", None, _envelope_to_wire(envelope)
        )

    async def route_lifecycle(
        self, target: WireRouteAddress | None, envelope: WireEnvelope
    ) -> Any:
        routed = _envelope_from_wire(envelope)
        if target is None:
            return await self._host.lifecycle.route(routed)
        return await self.route_to_target(
            RouteAddress(key=target["key"], data=target["data"]), routed
        )

    async def route_to_target(
        self, target: RouteAddress, envelope: RouteEnvelope
    ) -> Any:
        target_path = path_from_json(target.data)
        own = self.self_path
        if not is_same_prefix(own, target_path):
            raise ValueError(
                f"Route target {target.key!r} doesn't descend from this agent"
            )
        if len(target_path) == len(own):
            return await self._host.lifecycle.route(envelope)
        step = target_path[len(own)]
        if not self.registry.has(step.class_name, step.name):
            await self._cleanup_routed_work(target_path[: len(own) + 1])
            return False
        child = await self.resolve(step.class_name, step.name)
        wire_target = WireRouteAddress(key=target.key, data=target.data)
        return await _ffi.call_rpc(
            child, "_cf_route_lifecycle", wire_target, _envelope_to_wire(envelope)
        )

    async def route_request(
        self, request: Request, match: SubAgentPathMatch
    ) -> Response:
        decision = await self._before_sub_agent(request, match)
        upgrade = is_upgrade_request(request)
        if isinstance(decision, Response):
            if upgrade:
                code, reason = rejection_close(decision)
                return _ffi.websocket_rejection(code, reason)
            return decision
        forward = decision if isinstance(decision, Request) else request
        # The root keeps every client socket, so it accepts the upgrade itself
        # and forwards the socket's events.
        if upgrade:
            outer = rewrite_pathname(forward.url, urlsplit(request.url).path)
            accepted = _ffi.request_with(forward, headers={OUTER_URL_HEADER: outer})
            return await self._host.lifecycle.fetch(accepted)
        try:
            stub = await self.resolve(match.child_class, match.child_name)
        except InvalidSubAgentNameError as error:
            _log.error("Sub-agent route failed: %s", error)
            return Response("Bad Request", status=400)
        except UnknownSubAgentClassError as error:
            _log.error("Sub-agent route failed: %s", error)
            return Response("Not Found", status=404)
        forwarded = _ffi.request_with(
            forward, url=rewrite_pathname(forward.url, match.remaining_path)
        )
        return await _ffi.facet_fetch(stub, forwarded)

    async def _before_sub_agent(
        self,
        request: Request,
        match: SubAgentPathMatch,
        connection: Connection | None = None,
    ) -> Request | Response | None:
        route = AgentRoute(class_name=match.child_class, name=match.child_name)
        return await run_in_host_context(
            self._host,
            partial(self._host.on_before_sub_agent, request, route),
            connection=connection,
            request=request,
        )

    async def forward_connect(
        self, connection: Connection, request: Request, *, gate: bool
    ) -> bool:
        return await self._forward(
            connection,
            "_cf_handle_sub_agent_websocket_connect",
            create=True,
            request=request,
            gate=gate,
        )

    async def forward_message(
        self, connection: Connection, message: str | bytes
    ) -> bool:
        return await self._forward(
            connection, "_cf_handle_sub_agent_websocket_message", message
        )

    async def forward_close(
        self, connection: Connection, code: int, reason: str, was_clean: bool
    ) -> bool:
        return await self._forward(
            connection,
            "_cf_handle_sub_agent_websocket_close",
            code,
            reason,
            was_clean,
        )

    async def _forward(
        self,
        connection: Connection,
        method: str,
        *args: Any,
        create: bool = False,
        request: Request | None = None,
        gate: bool = False,
    ) -> bool:
        target = await self._resolve_connection(
            connection, create=create, request=request, gate=gate
        )
        if target is None:
            return False
        if not isinstance(target, tuple):
            return True

        child, meta = target
        # The bridge's proxies live only as long as this forwarded event.
        with _ffi.proxies() as scope:
            bridge = {
                "send": scope.rpc(partial(_bridge_send, connection)),
                "close": scope.rpc(partial(_bridge_close, connection)),
                "set_state": scope.rpc(partial(_bridge_set_state, connection)),
                "broadcast": scope.rpc(self._bridge_broadcast),
            }
            await _ffi.call_rpc(child, method, bridge, meta, *args)
        return True

    def _bridge_broadcast(
        self, path_json: str, message: str | bytes, without: list[str]
    ) -> None:
        self._spawn(self.broadcast_to_path(path_from_json(path_json), message, without))

    async def _resolve_connection(
        self,
        connection: Connection,
        *,
        create: bool,
        request: Request | None = None,
        gate: bool = False,
    ) -> tuple[Any, ForwardedConnection] | Literal["dropped"] | None:
        outer = connection._flag(OUTER_URL_FLAG)
        uri = outer if isinstance(outer, str) else connection.uri
        if not uri:
            return None
        if connection._flag(DELETED_FLAG):
            return _DROPPED
        known = _ffi.export_names(self._host.ctx)
        match = parse_sub_agent_path(uri, known)
        if match is None:
            return None
        forward = request
        if request is not None and gate:
            decision = await self._before_sub_agent(request, match, connection)
            if isinstance(decision, Response):
                connection.close(*rejection_close(decision))
                return _DROPPED
            if isinstance(decision, Request):
                forward = decision
        child = await (self.resolve if create else self._resolve_existing)(
            match.child_class, match.child_name
        )
        if child is None:
            return _DROPPED
        if forward is None:
            child_uri = rewrite_pathname(uri, match.remaining_path)
            return child, forwarded_meta(connection, child_uri, None)
        child_uri = rewrite_pathname(forward.url, match.remaining_path)
        headers = [
            [k, v] for k, v in forward.headers.items() if k.lower() != OUTER_URL_HEADER
        ]
        return child, forwarded_meta(connection, child_uri, headers)

    async def handle_connect(self, bridge: Any, meta: ForwardedConnection) -> None:
        connection = self._virtual_for(meta)

        async def connect() -> None:
            request = _ffi.make_request(
                meta["uri"] or "http://placeholder/", meta["request_headers"]
            )
            if await self.forward_connect(connection, request, gate=True):
                return
            await run_in_host_context(
                self._host,
                partial(self._host._connect_virtual, connection, request),
                connection=connection,
                request=request,
            )
            connection._set_flag(TAGS_FLAG, list(connection.tags))

        await self._in_frame(bridge, meta["id"], connect)

    async def handle_message(
        self, bridge: Any, meta: ForwardedConnection, message: str | bytes
    ) -> None:
        message = as_message(message)
        connection = self._virtual_for(meta)

        async def handle() -> None:
            if await self.forward_message(connection, message):
                return
            await run_in_host_context(
                self._host,
                partial(self._host._message_locally, connection, message),
                connection=connection,
            )

        await self._in_frame(bridge, meta["id"], handle)

    async def handle_close(
        self,
        bridge: Any,
        meta: ForwardedConnection,
        code: int,
        reason: str,
        was_clean: bool,
    ) -> None:
        connection = self._virtual_for(meta)

        async def close() -> None:
            if await self.forward_close(connection, code, reason, was_clean):
                return
            await run_in_host_context(
                self._host,
                partial(self._host._close_locally, connection, code, reason, was_clean),
                connection=connection,
            )

        await self._in_frame(bridge, meta["id"], close)
        self._virtual.pop(meta["id"], None)

    async def _in_frame(
        self, bridge: Any, connection_id: str, fn: Callable[[], Any]
    ) -> None:
        frame = _Frame(connection_id=connection_id, bridge=bridge)
        token = _frame.set(frame)
        try:
            await fn()
            # Deliver everything this event queued while its bridge is valid.
            await self._drain(connection_id)
        finally:
            frame.bridge = None
            _frame.reset(token)

    def _virtual_for(self, meta: ForwardedConnection) -> VirtualConnection:
        connection = self._virtual.get(meta["id"])
        if connection is None:
            connection = self._virtual[meta["id"]] = VirtualConnection(self, meta)
        else:
            connection._update(meta)
        return connection

    def route_operation(self, connection_id: str, operation: str, *args: Any) -> None:
        frame = _frame.get()
        previous = self._tails.get(connection_id)
        task = asyncio.ensure_future(
            self._run_operation(previous, frame, connection_id, operation, args)
        )
        self._tails[connection_id] = task
        task.add_done_callback(partial(self._operation_done, connection_id))

    async def _run_operation(
        self,
        previous: "asyncio.Task[None] | None",
        frame: _Frame | None,
        connection_id: str,
        operation: str,
        args: tuple[Any, ...],
    ) -> None:
        if previous is not None:
            await asyncio.gather(previous, return_exceptions=True)
        bridge = (
            frame.bridge
            if frame is not None and frame.connection_id == connection_id
            else None
        )
        if bridge is not None:
            await _ffi.call_rpc(bridge, operation, *args)
            return
        root_method = {
            "send": "_cf_send_to_sub_agent_connection",
            "close": "_cf_close_sub_agent_connection",
            "set_state": "_cf_set_sub_agent_connection_state",
        }[operation]
        await _ffi.call_rpc(self._root_stub(), root_method, connection_id, *args)

    def _operation_done(self, connection_id: str, task: "asyncio.Task[None]") -> None:
        if self._tails.get(connection_id) is task:
            del self._tails[connection_id]
        if not task.cancelled() and (error := task.exception()) is not None:
            _log.error(
                "Sub-agent connection operation failed for %r",
                connection_id,
                exc_info=error,
            )

    async def _drain(self, connection_id: str) -> None:
        while (tail := self._tails.get(connection_id)) is not None:
            await asyncio.gather(tail, return_exceptions=True)

    def virtual_connections(
        self, tag: str | None = None
    ) -> Iterator[VirtualConnection]:
        known = _ffi.export_names(self._host.ctx)
        for connection in list(self._virtual.values()):
            if tag is not None and tag not in connection.tags:
                continue
            uri = connection.uri
            passes_through_to_sub_agent = (
                uri and parse_sub_agent_path(uri, known) is not None
            )
            if passes_through_to_sub_agent:
                continue
            yield connection

    async def _load_root_connections(self) -> None:
        metas = await _ffi.call_rpc(
            self._root_stub(),
            "_cf_sub_agent_connection_metas",
            path_to_json(self.self_path),
        )
        for meta in metas or ():
            self._virtual.setdefault(meta["id"], VirtualConnection(self, meta))

    def connection_target(self, connection: Connection) -> list[AgentPathStep] | None:
        found = self._target_of(connection)
        return found[0] if found is not None else None

    def _target_of(
        self, connection: Connection, stop_at: Sequence[AgentPathStep] | None = None
    ) -> tuple[list[AgentPathStep], str] | None:
        outer = connection._flag(OUTER_URL_FLAG)
        if not isinstance(outer, str):
            return None
        known = _ffi.export_names(self._host.ctx)
        path = list(self.self_path)
        url = outer
        while (match := parse_sub_agent_path(url, known)) is not None:
            path.append(
                AgentPathStep(class_name=match.child_class, name=match.child_name)
            )
            url = rewrite_pathname(url, match.remaining_path)
            if stop_at is not None and path == list(stop_at):
                return path, url
        if stop_at is not None or len(path) == len(self.self_path):
            return None
        return path, url

    def is_child_targeted(self, connection: Connection) -> bool:
        return isinstance(connection._flag(OUTER_URL_FLAG), str)

    def root_connection(self, connection_id: str) -> Connection | None:
        for connection in self._host._websockets.get_connections(connection_id):
            if connection.id == connection_id and self.is_child_targeted(connection):
                return connection
        return None

    def connection_metas(self, owner: list[AgentPathStep]) -> list[ForwardedConnection]:
        return [
            forwarded_meta(connection, found[1], None)
            for connection in self._host._websockets.get_connections()
            if (found := self._target_of(connection, stop_at=owner)) is not None
        ]

    async def broadcast_to_path(
        self,
        owner: Sequence[AgentPathStep],
        message: str | bytes,
        without: Sequence[str] = (),
    ) -> None:
        if self.is_facet:
            # After this facet's queued operations, so it can't overtake them.
            await asyncio.gather(*self._tails.values(), return_exceptions=True)
            await _ffi.call_rpc(
                self._root_stub(),
                "_cf_broadcast_to_sub_agent",
                path_to_json(owner),
                message,
                list(without),
            )
            return
        owner_path, message = list(owner), as_message(message)
        for connection in self._host._websockets.get_connections():
            if connection.id in without:
                continue
            if self.connection_target(connection) == owner_path:
                _send(connection, message)

    def close_connections_under(
        self, prefix: Sequence[AgentPathStep], code: int, reason: str
    ) -> None:
        for connection in self._host._websockets.get_connections():
            target = self.connection_target(connection)
            if target is None or not is_same_prefix(prefix, target):
                continue
            # Marked before closing, so a frame already in flight can't
            # recreate the sub-agent.
            connection._set_flag(DELETED_FLAG, True)
            with suppress(_ffi.JsException):
                connection.close(code, reason)

    async def _close_connections_for(self, path: list[AgentPathStep]) -> None:
        if self.is_facet:
            await _ffi.call_rpc(
                self._root_stub(),
                "_cf_close_sub_agent_connections_for_prefix",
                path_to_json(path),
                1001,
                "Sub-agent deleted",
            )
        else:
            self.close_connections_under(path, 1001, "Sub-agent deleted")

    async def invoke(
        self, class_name: str, name: str, method: str, args: list[Any]
    ) -> Any:
        await self._host.lifecycle.start()
        stub = await self.resolve(class_name, name)
        return await _ffi.call_rpc(stub, method, *args)

    async def invoke_path(
        self, path: list[AgentPathStep], method: str, args: list[Any]
    ) -> Any:
        await self._host.lifecycle.start()
        own = self.self_path[-1]
        if not path or path[0] != own:
            raise ValueError(f"Path invocation reached {own} but expected {path[:1]}")
        if len(path) == 1:
            return await getattr(self._host, method)(*args)
        step = path[1]
        child = await self.resolve(step.class_name, step.name)
        if len(path) == 2:
            return await _ffi.call_rpc(child, method, *args)
        return await _ffi.call_rpc(
            child, "_cf_invoke_sub_agent_path", path_to_json(path[1:]), method, args
        )

    def _spawn(self, work: Any) -> None:
        task = asyncio.ensure_future(work)
        self._background.add(task)
        task.add_done_callback(self._background_done)

    def _background_done(self, task: "asyncio.Task[Any]") -> None:
        self._background.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            _log.warning("Sub-agent background work failed: %r", error)


def _facet_key(class_name: str, name: str) -> str:
    return f"{class_name}\0{name}"


def _envelope_to_wire(envelope: RouteEnvelope) -> WireEnvelope:
    source = envelope.source
    return WireEnvelope(
        capability=envelope.capability,
        source=WireRouteAddress(key=source.key, data=source.data)
        if source is not None
        else None,
        payload=envelope.payload,
    )


def _envelope_from_wire(envelope: WireEnvelope) -> RouteEnvelope:
    source = envelope["source"]
    return RouteEnvelope(
        capability=envelope["capability"],
        source=RouteAddress(key=source["key"], data=source["data"])
        if source is not None
        else None,
        payload=envelope["payload"],
    )


# Binary frames cross RPC as a memoryview.
def as_message(message: Any) -> str | bytes:
    return message if isinstance(message, str | bytes) else bytes(message)


def _bridge_send(connection: Connection, message: str | bytes) -> None:
    _send(connection, as_message(message))


def _bridge_close(connection: Connection, code: int | None, reason: str | None) -> None:
    with suppress(_ffi.JsException):
        connection.close(code, reason)


def _bridge_set_state(
    connection: Connection, state: Any, flags: dict[str, Any]
) -> None:
    apply_forwarded_state(connection, state, dict(flags or {}))
