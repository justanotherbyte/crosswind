---
title: "Host an MCP server in Python on Cloudflare Workers"
description: "Run an MCP server in Python on Cloudflare Workers with create_mcp_handler and the official mcp SDK: read env in tools, add auth, and set CORS and host checks."
---

`create_mcp_handler` serves an [MCP](https://modelcontextprotocol.io) server from your Worker. You write the server with the official `mcp` Python SDK, and we handle getting requests in and out of it on Workers.

The endpoint is stateless. It serves both 2026-07-28 requests and older handshake clients, and there are no sessions to manage.

## Installation

!!! note
    Python 3.14 Workers are required, because `mcp` 2.x doesn't install on earlier Pyodide versions. Set a compatibility date of 2026-09-08 or later, or add the `python_workers_314` compatibility flag.

Run the following in your terminal:
```sh
uv add "cf-agents[mcp-server]"
```

## Serve a tool

```python
from mcp.server import MCPServer
from workers import Request, Response, WorkerEntrypoint

from agents.mcp.server import create_mcp_handler


def build_server() -> MCPServer:
    server = MCPServer("Notes", version="1.0.0")

    @server.tool()
    async def shout(text: str) -> str:
        """Return the text in capitals."""
        return text.upper()

    return server


handler = create_mcp_handler(build_server)


class Default(WorkerEntrypoint):
    async def fetch(self, request: Request) -> Response:
        return await handler(request, self.env, self.ctx)
```

Your server now answers at `/mcp`, and any other path gets a `404`. Point any MCP client at it, including [one on an agent](mcp-client.md).

You can pass a server, which every request reuses, or a function that returns one, which runs once per request. If the function raises, the client gets a JSON-RPC `500`.

!!! warning
    Write tools as `async def`. The `mcp` SDK runs a plain `def` tool in a thread, and Python Workers run on Pyodide, which doesn't support starting one.

## Reach `env` from a tool

A function that builds the server is also how tools get to your bindings. Build the handler in `fetch` so it can close over `self.env`:

```python
from typing import Any

from mcp.server import MCPServer
from workers import Request, Response, WorkerEntrypoint

from agents.mcp.server import create_mcp_handler


def build_server(env: Any) -> MCPServer:
    server = MCPServer("Notes", version="1.0.0")

    @server.tool()
    async def read_note(key: str) -> str | None:
        """Read a note from KV."""
        return await env.NOTES.get(key)

    return server


class Default(WorkerEntrypoint):
    async def fetch(self, request: Request) -> Response:
        handler = create_mcp_handler(lambda: build_server(self.env))
        return await handler(request, self.env, self.ctx)
```

## Serve from an agent

Call `handler.fetch` from `on_request`, and your tools run inside the agent, so `get_current_agent()` works in them:

```python
from mcp.server import MCPServer
from workers import Request, Response

from agents import Agent, get_current_agent
from agents.mcp.server import create_mcp_handler

server = MCPServer("Notes", version="1.0.0")


@server.tool()
async def count() -> int:
    """Return this agent's count."""
    return get_current_agent().agent.state["count"]


class Counter(Agent):
    initial_state = {"count": 0}

    async def on_request(self, request: Request) -> Response:
        handler = create_mcp_handler(server, route=f"/agents/counter/{self.name}/mcp")
        return await handler.fetch(request)
```

`route` has to match the full path exactly, including the agent's name, so we build the handler per request.

## Add auth

The handler doesn't do auth itself. An `MCPServer` built with the SDK's OAuth settings raises `MCPServerConfigError`, because those add routes the handler doesn't serve. Check the request first, then tell your tools who it is with `auth_context`:

```python
from mcp.server import MCPServer
from workers import Request, Response, WorkerEntrypoint

from agents.mcp.server import McpAuthContext, create_mcp_handler, get_mcp_auth_context

server = MCPServer("Notes", version="1.0.0")


@server.tool()
async def whoami() -> str:
    """Return the signed-in user."""
    context = get_mcp_auth_context()
    return context.props["user"] if context else "anonymous"


class Default(WorkerEntrypoint):
    async def fetch(self, request: Request) -> Response:
        user = await verify_token(request.headers.get("Authorization"))
        if user is None:
            return Response("Unauthorized", status=401)

        handler = create_mcp_handler(server, auth_context=McpAuthContext(props={"user": user}))
        return await handler(request, self.env, self.ctx)
```

`verify_token` stands for your own code. `get_mcp_auth_context()` returns `None` outside a request, or when the handler has no `auth_context`.

## Options

| Parameter                  | Default         | Description |
| -------------------------- | --------------- | ----------- |
| `route`                    | `"/mcp"`        | The exact path served. |
| `cors`                     | `CORSOptions()` | The CORS headers on every response, preflights included. `None` adds none, and leaves `OPTIONS` to the server. |
| `allowed_hostnames`        | See below       | The `Host` header's allowed hostnames. |
| `allowed_origin_hostnames` | See below       | The `Origin` header's allowed hostnames, when one is present. `"*"` turns the check off, so only use that when something in front does the same check. |
| `auth_context`             | `None`          | What `get_mcp_auth_context()` returns. |
| `json_response`            | `False`         | Answer with JSON in place of SSE streams. |

A `Host` or `Origin` that fails its check gets a `403`.

| Check    | Allowed by default |
| -------- | ------------------ |
| `Host`   | Localhost names on a `localhost` endpoint, its own hostname on a `workers.dev` endpoint, and anything on any other endpoint. |
| `Origin` | Localhost names, a `workers.dev` endpoint's own hostname, and the hostname of a concrete `cors.origin`. |

On a custom domain, any `Host` is allowed, but a browser on your own site gets a `403` because its `Origin` isn't on the list. Set `allowed_origin_hostnames`, or a concrete `cors.origin`. Clients outside a browser send no `Origin`, so this doesn't affect them.

`CORSOptions` has `origin="*"`, `headers`, `methods="GET, POST, DELETE, OPTIONS"`, `expose_headers="mcp-session-id"` and `max_age=86400`.

## Limits

- There's no way to push updates, since the endpoint is stateless. The handler drops `subscriptions/listen`, `resources/subscribe` and `resources/unsubscribe` from the server.
- `McpAgent`, which is deprecated in the TypeScript SDK, isn't ported.
