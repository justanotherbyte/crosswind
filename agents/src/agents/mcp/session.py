"""There's no background task: each request is one POST.

See ``.design/mcp_engine.md`` §4. The wire facts are the ``mcp`` 2.3.0 SDK's
(``client/_probe.py``, ``client/session.py``, ``client/streamable_http.py``,
``shared/inbound.py``).
"""

import asyncio
import base64
import json
import re
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from typing import Any, Protocol

from ..core.encoding import to_json
from ..core.timing import to_seconds
from ..core.types import Duration
from .errors import (
    MCPOAuthError,
    MCPProtocolError,
    MCPRpcError,
    MCPTransportError,
)
from .http import (
    HttpRequest,
    HttpResponse,
    HttpTransport,
    read_body,
    send_within_origin,
)
from .sse import iter_events
from .types import SessionSnapshot

__all__ = (
    "HANDSHAKE_VERSIONS",
    "LATEST_HANDSHAKE_VERSION",
    "MODERN_VERSIONS",
    "McpSession",
    "SessionAuth",
)

# Oldest first: [-1] is the latest, and oauth_flow compares versions by index.
HANDSHAKE_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
MODERN_VERSIONS = ("2026-07-28",)
LATEST_HANDSHAKE_VERSION = HANDSHAKE_VERSIONS[-1]

# JSON-RPC error codes (mcp_types/jsonrpc.py).
_CONNECTION_CLOSED = -32000
_REQUEST_TIMEOUT = -32001
_UNSUPPORTED_PROTOCOL_VERSION = -32022
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INTERNAL_ERROR = -32603

_PROTOCOL_VERSION_META = "io.modelcontextprotocol/protocolVersion"
_CLIENT_INFO_META = "io.modelcontextprotocol/clientInfo"
_CLIENT_CAPABILITIES_META = "io.modelcontextprotocol/clientCapabilities"

# Method -> the params key mirrored in the Mcp-Name header (inbound.py:72).
_NAME_BEARING = {"tools/call": "name", "prompts/get": "name", "resources/read": "uri"}
_HEADER_SAFE = re.compile(r"[\x20-\x7e]*")
_BASE64_SENTINEL = re.compile(r"=\?base64\?.*\?=")

_DEFAULT_TIMEOUT = 60
_GET_RECONNECT_ATTEMPTS = 2
_GET_RECONNECT_DELAY_MS = 1000


class SessionAuth(Protocol):
    async def authorization(self) -> str | None: ...

    # Returning means the tokens were refreshed (only allowed with
    # allow_refresh), so the request is retried; otherwise this raises
    # MCPAuthorizationRequiredError.
    async def on_unauthorized(
        self,
        headers: Mapping[str, str],
        *,
        protocol_version: str | None,
        allow_refresh: bool,
    ) -> None: ...


class McpSession:
    def __init__(
        self,
        url: str,
        *,
        http: HttpTransport,
        client_info: Mapping[str, str],
        headers: Mapping[str, str],
        auth: SessionAuth | None,
        on_notification: Callable[[str, dict[str, Any]], None],
    ) -> None:
        self.url = url
        self._http = http
        self._client_info = dict(client_info)
        self._headers = dict(headers)
        self._auth = auth
        self._on_notification = on_notification
        self._next_id = 0
        self.protocol_version: str | None = None
        self.session_id: str | None = None
        self._result: dict[str, Any] = {}  # the initialize or discover result
        self._tool_headers: dict[str, dict[tuple[str, ...], str]] = {}
        self._retry_ms = _GET_RECONNECT_DELAY_MS

    @property
    def is_modern(self) -> bool:
        return self.protocol_version in MODERN_VERSIONS

    @property
    def capabilities(self) -> dict[str, Any]:
        return self._result.get("capabilities") or {}

    @property
    def instructions(self) -> str | None:
        return self._result.get("instructions")

    @property
    def snapshot(self) -> SessionSnapshot | None:
        if self.protocol_version is None:
            return None
        return SessionSnapshot(
            protocol_version=self.protocol_version,
            session_id=self.session_id,
            discover_result=self._result if self.is_modern else None,
        )

    # client/_probe.py: anything short of positive evidence of a modern
    # server falls back to the initialize handshake.
    async def connect(self, *, prior: SessionSnapshot | None = None) -> None:
        if (
            prior is not None
            and prior.protocol_version in MODERN_VERSIONS
            and prior.discover_result is not None
        ):
            self._adopt_discover(prior.discover_result)
            return

        version = MODERN_VERSIONS[-1]
        for attempt in range(2):
            try:
                result = await self._probe(version)
            except MCPRpcError as error:
                mutual = _mutual_modern(error)
                if error.code == _UNSUPPORTED_PROTOCOL_VERSION:
                    if mutual and attempt == 0:
                        version = mutual[-1]
                        continue
                    supported = _supported(error)
                    if supported is not None and not any(
                        v in HANDSHAKE_VERSIONS for v in supported
                    ):
                        raise
                retry = await self._initialize_or_reprobe(attempt)
                if retry is None:
                    return
                version = retry
                continue
            if not any(
                v in MODERN_VERSIONS for v in result.get("supportedVersions", ())
            ):
                # Speaks discover but advertises no modern version: legacy.
                await self._initialize()
                return
            self._adopt_discover(result)
            return

    async def _probe(self, version: str) -> dict[str, Any]:
        params = {
            "_meta": {
                _PROTOCOL_VERSION_META: version,
                _CLIENT_INFO_META: self._client_info,
                _CLIENT_CAPABILITIES_META: {},
            }
        }
        headers = {"mcp-protocol-version": version, "mcp-method": "server/discover"}
        result = await self._call(
            "server/discover", params, headers=headers, timeout=10
        )
        if not isinstance(result.get("capabilities"), dict):
            raise MCPRpcError(_INVALID_REQUEST, "unparseable server/discover result")
        return result

    async def _initialize_or_reprobe(self, attempt: int) -> str | None:
        # The handshake answering -32022 is itself modern evidence (a probe
        # that timed out but locked the era): re-probe once at a version it names.
        try:
            await self._initialize()
        except MCPRpcError as error:
            mutual = _mutual_modern(error)
            if (
                error.code != _UNSUPPORTED_PROTOCOL_VERSION
                or attempt != 0
                or not mutual
            ):
                raise
            return mutual[-1]
        return None

    async def _initialize(self) -> None:
        self.protocol_version = None
        self.session_id = None
        params = {
            "protocolVersion": LATEST_HANDSHAKE_VERSION,
            "capabilities": {},
            "clientInfo": self._client_info,
        }
        result = await self._call("initialize", params, headers={})
        version = result.get("protocolVersion")
        if version not in HANDSHAKE_VERSIONS:
            raise MCPProtocolError(
                f"Unsupported protocol version from server: {version}"
            )
        self.protocol_version = version
        self._result = result
        await self.notify("notifications/initialized")

    def _adopt_discover(self, result: dict[str, Any]) -> None:
        mutual = [
            v for v in MODERN_VERSIONS if v in result.get("supportedVersions", ())
        ]
        if not mutual:
            raise MCPProtocolError("No protocol version in common with the server")
        self.protocol_version = mutual[-1]
        self.session_id = None
        self._result = result

    async def request(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        timeout: Duration | None = _DEFAULT_TIMEOUT,
    ) -> dict[str, Any]:
        params, headers = self._stamp(method, dict(params or {}))
        try:
            result = await self._call(method, params, headers=headers, timeout=timeout)
        except MCPRpcError as error:
            if not self._session_expired(error):
                raise
            # The server forgot the session (it restarted): handshake again.
            await self._initialize()
            params, headers = self._stamp(method, dict(params))
            result = await self._call(method, params, headers=headers, timeout=timeout)
        if result.get("resultType") == "input_required":
            raise MCPProtocolError(f"{method} needs input; elicitation isn't supported")
        return result

    # Only the handshake era has client notifications, so no envelope is stamped.
    async def notify(
        self, method: str, params: Mapping[str, Any] | None = None
    ) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params:
            message["params"] = dict(params)
        await self._post(message, headers=self._era_headers(), expect_reply=False)

    def _stamp(
        self, method: str, params: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, str]]:
        # client/session.py:146-190
        headers = self._era_headers()
        if not self.is_modern:
            return params, headers
        meta = dict(params.get("_meta") or {})
        meta[_PROTOCOL_VERSION_META] = self.protocol_version
        meta[_CLIENT_INFO_META] = self._client_info
        meta[_CLIENT_CAPABILITIES_META] = {}
        params["_meta"] = meta
        headers["mcp-method"] = method
        name = params.get(_NAME_BEARING.get(method, ""))
        if isinstance(name, str):
            headers["mcp-name"] = _encode_header_value(name)
        if method == "tools/call" and isinstance(name, str):
            headers.update(self._param_headers(name, params.get("arguments") or {}))
        return params, headers

    def _era_headers(self) -> dict[str, str]:
        headers = {}
        if self.protocol_version is not None:
            headers["mcp-protocol-version"] = self.protocol_version
        if self.session_id is not None:
            headers["mcp-session-id"] = self.session_id
        return headers

    def _session_expired(self, error: MCPRpcError) -> bool:
        return error.data == _SESSION_EXPIRED and self.session_id is not None

    async def _call(
        self,
        method: str,
        params: Mapping[str, Any],
        *,
        headers: Mapping[str, str],
        timeout: Duration | None = _DEFAULT_TIMEOUT,
    ) -> dict[str, Any]:
        self._next_id += 1
        message = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params:
            message["params"] = dict(params)
        try:
            async with asyncio.timeout(
                None if timeout is None else to_seconds(timeout)
            ):
                reply = await self._post(message, headers=headers, expect_reply=True)
        except TimeoutError:
            raise MCPRpcError(_REQUEST_TIMEOUT, f"{method} timed out") from None
        if reply is None:
            raise MCPRpcError(_CONNECTION_CLOSED, "the server sent no response")
        if "error" in reply:
            error = reply["error"] or {}
            raise MCPRpcError(
                error.get("code", _INTERNAL_ERROR),
                error.get("message", ""),
                error.get("data"),
            )
        return reply.get("result") or {}

    async def _post(
        self,
        message: dict[str, Any],
        *,
        headers: Mapping[str, str],
        expect_reply: bool,
    ) -> dict[str, Any] | None:
        # Retry a 401 once after a refresh; a second one must authorize.
        for allow_refresh in (True, False):
            request = HttpRequest(
                method="POST",
                url=self.url,
                headers=await self._request_headers(headers),
                body=to_json(message),
            )
            async with send_within_origin(self._http, request) as response:
                if response.status == 401:
                    await self._unauthorized(response, allow_refresh=allow_refresh)
                    continue
                if message.get("method") == "initialize":
                    self.session_id = response.headers.get("mcp-session-id")
                return await self._read_reply(response, message, expect_reply)
        raise MCPOAuthError("the server rejected the refreshed tokens")

    async def _request_headers(self, extra: Mapping[str, str]) -> dict[str, str]:
        headers = {
            **self._headers,
            "accept": "application/json, text/event-stream",
            "content-type": "application/json",
            **extra,
        }
        if self._auth is not None:
            authorization = await self._auth.authorization()
            if authorization is not None:
                headers["authorization"] = authorization
        return headers

    async def _unauthorized(
        self, response: HttpResponse, *, allow_refresh: bool
    ) -> None:
        if self._auth is None:
            raise MCPOAuthError(
                "the server requires OAuth, and no callback URL is configured"
            )
        await self._auth.on_unauthorized(
            response.headers,
            protocol_version=self.protocol_version,
            allow_refresh=allow_refresh,
        )

    async def _read_reply(
        self,
        response: HttpResponse,
        message: Mapping[str, Any],
        expect_reply: bool,
    ) -> dict[str, Any] | None:
        # streamable_http.py:363-427
        status = response.status
        if 300 <= status < 400:
            location = response.headers.get("location", "")
            raise MCPRpcError(_INVALID_REQUEST, f"Redirect to {location} not followed")
        if status >= 400:
            raise await self._status_error(response)
        if not expect_reply:
            return None
        if status == 202:
            raise MCPRpcError(
                _INVALID_REQUEST, "the server answered a request with 202"
            )

        content_type = response.headers.get("content-type", "").lower()
        if content_type.startswith("application/json"):
            return next(_messages(await read_body(response)), None)
        if content_type.startswith("text/event-stream"):
            return await self._read_stream_until_reply(response.chunks(), message["id"])
        raise MCPRpcError(_INVALID_REQUEST, f"Unexpected content type {content_type!r}")

    async def _status_error(self, response: HttpResponse) -> MCPRpcError:
        if response.status == 404 and self.session_id is not None:
            return MCPRpcError(_INVALID_REQUEST, "Session terminated", _SESSION_EXPIRED)
        # A JSON-RPC error body wins over the status (a 400 with -32602, say).
        if (
            response.headers.get("content-type", "")
            .lower()
            .startswith("application/json")
        ):
            body = await read_body(response)
            try:
                reply = json.loads(body)
            except json.JSONDecodeError:
                reply = None
            if isinstance(reply, dict) and isinstance(reply.get("error"), dict):
                error = reply["error"]
                return MCPRpcError(
                    error.get("code", _INTERNAL_ERROR),
                    error.get("message", ""),
                    error.get("data"),
                )
        if response.status == 404:
            return MCPRpcError(_METHOD_NOT_FOUND, "Not Found")
        return MCPRpcError(
            _INTERNAL_ERROR, f"Server returned an error response ({response.status})"
        )

    async def _read_stream_until_reply(
        self, chunks: AsyncIterator[bytes], request_id: int | None
    ) -> dict[str, Any] | None:
        async for event in iter_events(chunks):
            if event.event != "message" or not event.data:
                continue  # a priming event
            for incoming in _messages(event.data):
                if "method" in incoming:
                    await self._dispatch_unsolicited(incoming)
                elif incoming.get("id") == request_id:
                    return incoming
        return None

    def _notify(self, notification: Mapping[str, Any]) -> None:
        self._on_notification(notification["method"], notification.get("params") or {})

    async def _answer(self, request: Mapping[str, Any]) -> None:
        # Server-initiated requests (handshake era). Only ping is supported.
        reply: dict[str, Any] = {"jsonrpc": "2.0", "id": request["id"]}
        if request["method"] == "ping":
            reply["result"] = {}
        else:
            reply["error"] = {"code": _METHOD_NOT_FOUND, "message": "Method not found"}
        await self._post(reply, headers=self._era_headers(), expect_reply=False)

    async def list_all(self, method: str, key: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        cursor = None
        while True:
            page = await self.request(method, {"cursor": cursor} if cursor else None)
            items.extend(page.get(key) or [])
            cursor = page.get("nextCursor")
            if not cursor or cursor in seen:
                break
            seen.add(cursor)
        if method == "tools/list":
            self._remember_tools(items)
        return items

    def _remember_tools(self, tools: list[dict[str, Any]]) -> None:
        # Each tool's x-mcp-header properties, mirrored on its calls.
        self._tool_headers = {
            tool["name"]: dict(_header_paths(tool.get("inputSchema")))
            for tool in tools
            if isinstance(tool.get("name"), str)
        }

    def _param_headers(self, name: str, arguments: Mapping[str, Any]) -> dict[str, str]:
        # inbound.py:276: Mcp-Param-<token> mirrors each annotated argument.
        headers = {}
        for path, token in self._tool_headers.get(name, {}).items():
            value: Any = arguments
            for key in path:
                value = value.get(key) if isinstance(value, Mapping) else None
            if isinstance(value, bool):
                rendered = "true" if value else "false"
            elif isinstance(value, str | int | float):
                rendered = str(value)
            else:
                continue
            headers[f"Mcp-Param-{token}"] = _encode_header_value(rendered)
        return headers

    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        timeout: Duration | None = _DEFAULT_TIMEOUT,
    ) -> dict[str, Any]:
        params = {"name": name, "arguments": dict(arguments)}
        return await self.request("tools/call", params, timeout=timeout)

    async def get_prompt(
        self, name: str, arguments: Mapping[str, str] | None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"name": name}
        if arguments:
            params["arguments"] = dict(arguments)
        return await self.request("prompts/get", params)

    async def read_resource(self, uri: str) -> dict[str, Any]:
        return await self.request("resources/read", {"uri": uri})

    async def listen(self) -> None:
        if self.is_modern:
            await self._listen_modern()
        elif self.session_id is not None:
            await self._listen_get()

    async def _listen_modern(self) -> None:
        wanted = {
            f"{kind}ListChanged": True
            for kind in ("tools", "prompts", "resources")
            if (self.capabilities.get(kind) or {}).get("listChanged")
        }
        if wanted:
            await self.request(
                "subscriptions/listen", {"notifications": wanted}, timeout=None
            )

    async def _listen_get(self) -> None:
        # streamable_http.py:224-262: reopen the stream when it ends; give up
        # after two failures (or empty streams) in a row.
        failures = 0
        while True:
            try:
                received = await self._read_get_stream()
            except (MCPTransportError, MCPRpcError):
                failures += 1
                if failures >= _GET_RECONNECT_ATTEMPTS:
                    raise
            else:
                if received is None:
                    return  # the server offers no stream
                failures = 0 if received else failures + 1
                if failures >= _GET_RECONNECT_ATTEMPTS:
                    return
            await asyncio.sleep(self._retry_ms / 1000)

    async def _read_get_stream(self) -> bool | None:
        headers = await self._request_headers(self._era_headers())
        headers["accept"] = "text/event-stream"
        del headers["content-type"]
        request = HttpRequest(method="GET", url=self.url, headers=headers)
        received = False
        async with send_within_origin(self._http, request) as response:
            if response.status == 405:
                return None
            if response.status >= 400:
                raise await self._status_error(response)
            async for event in iter_events(response.chunks()):
                self._retry_ms = event.retry_ms or self._retry_ms
                received = True
                if event.data:
                    for incoming in _messages(event.data):
                        await self._dispatch_unsolicited(incoming)
        return received

    async def _dispatch_unsolicited(self, incoming: Mapping[str, Any]) -> None:
        if "method" not in incoming:
            return
        if "id" in incoming:
            await self._answer(incoming)
        else:
            self._notify(incoming)

    async def close(self) -> None:
        if self.session_id is None:
            return
        headers = await self._request_headers(self._era_headers())
        request = HttpRequest(method="DELETE", url=self.url, headers=headers)
        async with send_within_origin(self._http, request):
            pass  # any answer ends it; 405 means the server doesn't support DELETE
        self.session_id = None


_SESSION_EXPIRED = "session-expired"


def _messages(data: str) -> Iterator[dict[str, Any]]:
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        raise MCPProtocolError("the server sent invalid JSON") from None
    for item in parsed if isinstance(parsed, list) else [parsed]:
        if isinstance(item, dict):
            yield item


def _supported(error: MCPRpcError) -> list[str] | None:
    data = error.data
    supported = data.get("supported") if isinstance(data, dict) else None
    return supported if isinstance(supported, list) else None


def _mutual_modern(error: MCPRpcError) -> list[str]:
    if error.code != _UNSUPPORTED_PROTOCOL_VERSION:
        return []
    supported = _supported(error) or []
    return [v for v in MODERN_VERSIONS if v in supported]


def _encode_header_value(value: str) -> str:
    # inbound.py:149: values that wouldn't survive as a header are base64-wrapped.
    if (
        _HEADER_SAFE.fullmatch(value)
        and value == value.strip()
        and not _BASE64_SENTINEL.fullmatch(value)
    ):
        return value
    return f"=?base64?{base64.b64encode(value.encode()).decode('ascii')}?="


def _header_paths(
    schema: Any, path: tuple[str, ...] = ()
) -> Iterator[tuple[tuple[str, ...], str]]:
    # Only properties reached through a chain of "properties" count (inbound.py:250).
    if not isinstance(schema, dict):
        return
    token = schema.get("x-mcp-header")
    if path and isinstance(token, str):
        yield path, token
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for name, sub in properties.items():
            yield from _header_paths(sub, (*path, name))
