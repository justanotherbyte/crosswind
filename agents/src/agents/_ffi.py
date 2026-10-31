"""The only SDK module that imports ``js`` / ``pyodide``.

Everything else imports and unit-tests under CPython, with tests replacing this
module. The conversion rules are specified in ``.design/utilities.md`` §3.

Storage, SQL parameters, and hibernation attachments use structured clone, so
they go through `py_to_js` / `js_to_py`. Native DO RPC goes through the Workers
SDK's own converters, wrapped by `to_rpc` / `from_rpc`.
"""

import json
import logging
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Awaitable,
    Callable,
    Generator,
    Mapping,
)
from contextlib import asynccontextmanager, contextmanager, suppress
from datetime import UTC, datetime
from typing import Any

import js
from pyodide.ffi import (
    JsException,
    JsProxy,
    create_once_callable,
    create_proxy,
    to_js,
)
from workers import Request, Response, python_from_rpc, python_to_rpc

try:
    from pyodide.ffi import jsnull
except ImportError:
    # Pyodide 0.27 (Python 3.12 Workers) has no jsnull: JS null arrives as
    # None, and None leaves as undefined, which storage, SQL and attachments
    # treat as null except for a top-level storage.put (no caller does that).
    jsnull = None

__all__ = (
    "ConsoleHandler",
    "FetchResponse",
    "JsException",
    "JsProxy",
    "ProxyScope",
    "abort_without_alarm_retry",
    "block_concurrency_while",
    "call_rpc",
    "clone_request",
    "env_binding_names",
    "export_names",
    "facet_abort",
    "facet_delete",
    "facet_fetch",
    "facet_get",
    "from_rpc",
    "has_namespace",
    "js_to_py",
    "kv_keys",
    "make_request",
    "namespace_stub",
    "open_fetch",
    "proxies",
    "py_to_js",
    "read_attachment",
    "request_with",
    "send_message",
    "streaming_response",
    "to_rpc",
    "unwrap",
    "websocket_error_response",
    "websocket_pair",
    "websocket_rejection",
    "with_headers",
    "write_attachment",
)

_TYPED_ARRAY_TAGS = frozenset(
    {
        "[object ArrayBuffer]",
        "[object Uint8Array]",
        "[object Int8Array]",
        "[object Uint8ClampedArray]",
        "[object Uint16Array]",
        "[object Int16Array]",
        "[object Uint32Array]",
        "[object Int32Array]",
        "[object Float32Array]",
        "[object Float64Array]",
        "[object DataView]",
    }
)


def py_to_js(value: Any) -> Any:
    return to_js(
        _prepare_for_to_js(value),
        dict_converter=js.Object.fromEntries,
        create_pyproxies=False,
    )


def _prepare_for_to_js(value: Any) -> Any:
    if value is None:
        return jsnull
    if isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise TypeError("naive datetime; use a timezone-aware datetime")
        return js.Date.new(value.timestamp() * 1000)
    if isinstance(value, list):
        return [_prepare_for_to_js(item) for item in value]
    if isinstance(value, dict):
        return {
            _require_str_key(key): _prepare_for_to_js(item)
            for key, item in value.items()
        }
    raise TypeError(f"{type(value).__name__} can't be stored as a JS value")


def _require_str_key(key: object) -> str:
    if not isinstance(key, str):
        raise TypeError(f"dict keys must be str, not {type(key).__name__}")
    return key


def js_to_py(value: Any) -> Any:
    if value is jsnull or value is None:
        return None
    if not isinstance(value, JsProxy):
        return value
    tag = str(js.Object.prototype.toString.call(value))
    if tag == "[object Array]":
        return [js_to_py(item) for item in value]
    if tag == "[object Object]":
        return {str(key): js_to_py(item) for key, item in js.Object.entries(value)}
    if tag in _TYPED_ARRAY_TAGS:
        return value.to_bytes()
    if tag == "[object Date]":
        return datetime.fromtimestamp(value.getTime() / 1000, UTC)
    raise TypeError(f"can't convert JS {tag} to a Python value")


def to_rpc(value: Any) -> Any:
    return python_to_rpc(value)


def from_rpc(value: Any) -> Any:
    return python_from_rpc(value)


# The SDK wraps ctx.storage and env bindings to convert every call; hot paths
# use the raw object with py_to_js / js_to_py instead (.design/utilities.md
# §3.8). _binding is the SDK's private attribute.
def unwrap(value: Any) -> Any:
    return getattr(value, "_binding", value)


class ProxyScope:
    __slots__ = ("_proxies",)

    def __init__(self) -> None:
        self._proxies: list[JsProxy] = []

    def proxy(self, fn: Callable[..., Any]) -> JsProxy:
        handle = create_proxy(fn)
        self._proxies.append(handle)
        return handle

    # fn must be synchronous: its result is converted before JS receives it.
    def rpc(self, fn: Callable[..., Any]) -> JsProxy:
        def call(*args: Any) -> Any:
            return to_rpc(fn(*(from_rpc(arg) for arg in args)))

        return self.proxy(call)

    def _destroy(self) -> None:
        proxies, self._proxies = self._proxies, []
        for handle in proxies:
            handle.destroy()


@contextmanager
def proxies() -> Generator[ProxyScope]:
    scope = ProxyScope()
    try:
        yield scope
    finally:
        scope._destroy()


# fn must not raise: an exception inside the callback leaves the object unusable
# (.design/platform_verification.md §2.9), so callers catch inside fn and
# re-raise after this returns.
async def block_concurrency_while[T](ctx: Any, fn: Callable[[], Awaitable[T]]) -> T:
    with proxies() as scope:
        return await ctx.blockConcurrencyWhile(scope.proxy(fn))


def abort_without_alarm_retry(ctx: Any, reason: str) -> None:
    # The Workers SDK's ctx.abort(reason) takes no options, so call the raw JS
    # abort.
    raw = _raw_ctx(ctx)
    options = py_to_js({"retryAlarm": False})
    # Deferred so the current invocation settles first
    # (.design/platform_verification.md §7.5).
    js.setTimeout(create_once_callable(lambda: raw.abort(reason, options)), 0)


# Browser devtools don't show an HTTP error body for an upgrade, so (as
# upstream) send the error as a frame and close the socket with 1011.
def websocket_error_response(message: str) -> Any:
    client, server = websocket_pair()
    server.accept()
    server.send(json.dumps({"error": message}))
    server.close(1011, "Uncaught exception during session setup")
    return Response(None, status=101, web_socket=client)


def websocket_pair() -> tuple[JsProxy, JsProxy]:
    client, server = js.WebSocketPair.new().object_values()
    return client, server


def read_attachment(ws: JsProxy) -> Any:
    return js_to_py(ws.deserializeAttachment())


# The runtime raises JsException for an attachment over 16 KiB.
def write_attachment(ws: JsProxy, value: Any) -> None:
    ws.serializeAttachment(py_to_js(value))


def send_message(ws: JsProxy, message: str | bytes) -> None:
    ws.send(message if isinstance(message, str) else py_to_js(message))


# Python's own output reaches Workers Logs at level error whatever the record's
# level, so write through the matching JS console method
# (.design/observability.md §6).
class ConsoleHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        message = self.format(record)
        if record.levelno >= logging.ERROR:
            js.console.error(message)
        elif record.levelno >= logging.WARNING:
            js.console.warn(message)
        elif record.levelno >= logging.INFO:
            js.console.info(message)
        else:
            js.console.debug(message)


def _install_console_handler() -> None:
    logger = logging.getLogger("agents")
    if not any(isinstance(handler, ConsoleHandler) for handler in logger.handlers):
        logger.addHandler(ConsoleHandler())


# This module only loads on Workers, so the handler is only installed there.
_install_console_handler()


def env_binding_names(env: Any) -> list[str]:
    return [str(name) for name in js.Object.keys(env._env)]


def clone_request(request: Request) -> Request:
    return Request(request.js_object.clone())


def with_headers(response: Response, headers: dict[str, str]) -> Response:
    # Fetched responses have immutable headers, so set them on a copy.
    copy = js.Response.new(response.js_object.body, response.js_object)
    for name, value in headers.items():
        copy.headers.set(name, value)
    return Response(copy)


def streaming_response(
    pieces: AsyncIterator[str], *, headers: dict[str, str]
) -> Response:
    encoder = js.TextEncoder.new()
    callbacks: list[JsProxy] = []

    def release() -> None:
        while callbacks:
            callbacks.pop().destroy()

    async def pull(controller: Any) -> None:
        try:
            piece = await anext(pieces)
        except StopAsyncIteration:
            controller.close()
            release()
            return
        controller.enqueue(encoder.encode(piece))

    async def cancel(_reason: Any) -> None:
        release()
        await pieces.aclose()  # ty: ignore[unresolved-attribute]

    callbacks.extend((create_proxy(pull), create_proxy(cancel)))
    source = to_js(
        {"pull": callbacks[0], "cancel": callbacks[1]},
        dict_converter=js.Object.fromEntries,
    )
    return Response(js.ReadableStream.new(source), headers=headers)


async def kv_keys(storage: Any, prefix: str) -> list[str]:
    entries = await unwrap(storage).list(py_to_js({"prefix": prefix}))
    return [str(key) for key in entries.keys()]  # noqa: SIM118  (a JS Map iterates pairs)


# Outbound HTTP (.design/platform_verification.md §2.8)


class FetchResponse:
    __slots__ = ("_reader", "_response", "headers", "status")

    def __init__(self, response: JsProxy) -> None:
        self._response = response
        self._reader: JsProxy | None = None
        self.status: int = int(response.status)
        # JS Headers lower-case the names.
        self.headers: dict[str, str] = dict(
            js.Object.fromEntries(response.headers).to_py()
        )

    # A body stream can be read only once.
    async def chunks(self) -> AsyncIterator[bytes]:
        body = self._response.body
        if body is None:
            return
        self._reader = reader = body.getReader()
        while True:
            result = await reader.read()
            if result.done:
                return
            yield result.value.to_bytes()

    async def _cancel(self) -> None:
        if self._reader is not None:
            with suppress(JsException):  # rejected once the stream has errored
                await self._reader.cancel()


@asynccontextmanager
async def open_fetch(
    method: str, url: str, *, headers: Mapping[str, str], body: str | None
) -> AsyncGenerator[FetchResponse]:
    # On Workers only an AbortController and a cancelled body reader abort a
    # request, so use both when the task is cancelled or the block exits early.
    controller = js.AbortController.new()
    init = to_js(
        {
            "method": method,
            "headers": dict(headers),
            "body": body,
            "signal": controller.signal,
            "redirect": "manual",
        },
        dict_converter=js.Object.fromEntries,
    )
    response: FetchResponse | None = None
    try:
        response = FetchResponse(await js.fetch(url, init))
        yield response
    finally:
        if response is not None:
            await response._cancel()
        controller.abort()


# Facets (sub-agents; .design/subagents_engine.md §1)


def _raw_ctx(ctx: Any) -> Any:
    return getattr(ctx, "_ctx", ctx)


def export_names(ctx: Any) -> list[str]:
    return [str(name) for name in js.Object.keys(_raw_ctx(ctx).exports)]


def has_namespace(ctx: Any, class_name: str) -> bool:
    exported = getattr(_raw_ctx(ctx).exports, class_name, None)
    return exported is not None and hasattr(exported, "idFromName")


def namespace_stub(ctx: Any, class_name: str, name: str) -> Any:
    namespace = getattr(_raw_ctx(ctx).exports, class_name)
    return namespace.get(namespace.idFromName(name))


def facet_get(
    ctx: Any, key: str, class_name: str, root_class: str, identity: str
) -> Any:
    raw = _raw_ctx(ctx)

    def getter() -> Any:
        # An id from the root's namespace makes the facet's ctx.id.name identity.
        namespace = getattr(raw.exports, root_class)
        return to_js(
            {
                "class": getattr(raw.exports, class_name),
                "id": namespace.idFromName(identity),
            },
            dict_converter=js.Object.fromEntries,
        )

    # The runtime may call the getter whenever the facet restarts, so the
    # proxy lives as long as the context.
    getters: dict[str, Any] = ctx.__dict__.setdefault("_agents_facet_getters", {})
    getters[key] = create_proxy(getter)
    return raw.facets.get(key, getters[key])


# Pending calls reject with reason; unlike facet_delete, the storage is kept.
def facet_abort(ctx: Any, key: str, reason: Exception) -> None:
    _raw_ctx(ctx).facets.abort(key, js.Error.new(str(reason)))


def facet_delete(ctx: Any, key: str) -> None:
    with suppress(JsException):  # thrown for a key that was never created
        _raw_ctx(ctx).facets.delete(key)


async def call_rpc(stub: Any, method: str, *args: Any) -> Any:
    # A raw dict passed to a JS function would arrive as a Python proxy and
    # fail to serialize.
    result = await getattr(stub, method)(*(to_rpc(arg) for arg in args))
    return from_rpc(result)


async def facet_fetch(stub: Any, request: Request) -> Response:
    return Response(await stub.fetch(request.js_object))


def request_with(
    request: Request,
    *,
    url: str | None = None,
    headers: dict[str, str] | None = None,
    drop: str | None = None,
) -> Request:
    copy = js.Request.new(url if url is not None else request.url, request.js_object)
    for name, value in (headers or {}).items():
        copy.headers.set(name, value)
    if drop is not None:
        copy.headers.delete(drop)
    return Request(copy)


def make_request(url: str, headers: list[list[str]] | None) -> Request:
    init = js.Object.new()
    init.headers = js.Headers.new(to_js(headers or []))
    return Request(js.Request.new(url, init))


# A browser can't read a failed handshake's response, so a refused /sub/
# connection gets a close frame instead.
def websocket_rejection(code: int, reason: str) -> Response:
    client, server = websocket_pair()
    server.accept()
    server.close(code, reason)
    return Response(None, status=101, web_socket=client)
