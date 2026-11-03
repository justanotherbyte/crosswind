---
title: "Connect an agent to MCP servers"
description: "Turn on the MCP client, add a server, handle OAuth, list and call its tools, restore connections after a wake, and handle MCP errors."
---

Your agent can connect to any remote [MCP](https://modelcontextprotocol.io) server, then list and call its tools, prompts and resources. Connections are stored in the agent's SQLite database, so they come back when the agent wakes.

The client doesn't need the `mcp-server` extra, and it works on every Python version `cf-agents` supports. To serve tools instead, see [Host an MCP server](mcp-server.md).

## Turn it on

MCP is off by default, so an agent that doesn't use it never imports the client:

```python
from agents import Agent, AgentOptions, MCPOptions, callable
from agents.mcp import TextContent


class Researcher(Agent):
    options = AgentOptions(mcp=MCPOptions())

    @callable
    async def connect(self, url: str) -> str | None:
        result = await self.add_mcp_server("Docs", url, id="docs")
        return result.auth_url

    @callable
    async def tools(self) -> list[str]:
        return [entry.tool.name for entry in self.mcp.list_tools(server_id="docs")]

    @callable
    async def add(self, a: int, b: int) -> str:
        result = await self.mcp.call_tool("docs", "add", {"a": a, "b": b})
        if result.is_error:
            raise RuntimeError(f"add failed: {result.content}")
        return "".join(
            block.text for block in result.content if isinstance(block, TextContent)
        )
```

Against a server with an `add` tool, `connect` returns `None`, `tools` returns a list containing `"add"`, and `add(1, 2)` returns `"3"`.

Without `mcp=MCPOptions()`, `self.mcp`, `add_mcp_server`, `remove_mcp_server` and `get_mcp_servers` raise `MCPNotEnabledError`. It has to be set on the class, because the agent needs to restore its servers during startup.

## Add a server

`add_mcp_server` stores the server, connects and loads its catalog. It's safe to call twice, as the same name and URL give you the existing server back.

```python
result = await self.add_mcp_server("Docs", "https://docs.example.com/mcp", id="docs")
print(result.state)  # "ready", or "authenticating"
```

| Parameter       | Default     | Description |
| --------------- | ----------- | ----------- |
| `name`          | Required    | A display name. |
| `url`           | Required    | The server's Streamable HTTP endpoint. |
| `id`            | Random      | A stable id, which you'll want if you call tools by id. It's normalized to lowercase `[a-z0-9_-]`, up to 64 characters. An id belonging to a different name or URL raises `MCPServerIdConflictError`. |
| `headers`       | `None`      | Sent on every request. Use these for a bearer token or Cloudflare Access. |
| `transport`     | `"auto"`    | `"auto"` or `"streamable-http"`. Both are Streamable HTTP, since the legacy SSE transport isn't supported yet. |
| `callback_host` | The request's origin | The OAuth callback's origin. |
| `callback_path` | `/agents/{agent}/{name}/callback` | The callback's path. With `callback_host` and `send_identity_on_connect=False`, leaving this out raises `ValueError`, because the default contains the instance name. |
| `agents_prefix` | `"agents"`  | The routing prefix in the default callback path. |
| `retry`         | `None`      | [`RetryOptions`](agent-class.md#retryoptions) for connecting when the server is restored, or after an OAuth callback. |

!!! warning
    `headers` are stored in the agent's database, in plain text. Don't put a secret there that you wouldn't store in the agent yourself.

URLs that point at a private address raise `MCPBlockedUrlError` before anything is stored. That covers RFC 1918, link-local, unique-local and cloud metadata addresses. Loopback is allowed for local development.

If the connection fails, you get `MCPConnectionError` and the server stays stored. Call `await self.mcp.connect_server(id)` to try again, or `await self.remove_mcp_server(id)` to delete it along with its OAuth state.

## Handle OAuth

If the server needs OAuth, `add_mcp_server` returns `state="authenticating"` with an `auth_url`. Send your user there. The provider redirects back to the callback URL, the agent finishes the connection in the background, and connected clients get the update.

The callback URL needs an origin, which comes from the current request or connection. **Call `add_mcp_server` from `on_request` or a `@callable` for an OAuth server.** From a schedule or a queue there's no request to read it from, so pass `callback_host` yourself.

After the callback, the agent redirects to your site's root. Set your own pages with `MCPOptions`:

```python
options = AgentOptions(
    mcp=MCPOptions(oauth_success_redirect="/connected", oauth_error_redirect="/failed")
)
```

The error page gets `?error=<message>`. For anything more, override a hook:

| Hook | Use it to |
| ---- | --------- |
| `async on_mcp_oauth_callback(result, request)` | Return your own `Response` in place of the redirect. `result` has `server_id`, `success` and `error`. `error` can contain text from the provider, so escape it before you show it. |
| `create_mcp_oauth_provider(callback_url)` | Return your own `MCPOAuthProvider`, for pre-registered client credentials. The default registers itself with the server and keeps its tokens in the agent's storage. |

## Use a server

These are on `self.mcp`, an `MCPClientManager`:

| Method | Returns |
| ------ | ------- |
| `list_tools(*, server_id=None, server_name=None, state=None)` | `ServerTool`s, each with `server_id` and `tool`. |
| `list_prompts(...)`, `list_resources(...)`, `list_resource_templates(...)` | The same, with the same filters. |
| `async call_tool(server_id, name, arguments=None, *, timeout=None)` | `CallToolResult`. The timeout defaults to 60 seconds. |
| `async read_resource(server_id, uri)` | `ReadResourceResult` |
| `async get_prompt(server_id, name, arguments=None)` | `GetPromptResult` |
| `async discover(id, *, timeout=None)` | Reloads a server's catalog. |
| `list_servers()` | The stored `MCPServerRecord`s. |

A tool that fails gives you `is_error=True`, not an exception. Exceptions mean the call itself didn't work, such as `MCPRpcError` or `MCPTransportError`.

### Give the tools to a model

We don't wire these into [`AIChatAgent`](chat.md) or a [harness](harness.md) for you. `tool.input_schema` is plain JSON Schema, so most model APIs can take it directly:

```python
schemas = [
    {
        "type": "function",
        "function": {
            "name": entry.tool.name,
            "description": entry.tool.description or "",
            "parameters": entry.tool.input_schema,
        },
    }
    for entry in self.mcp.list_tools()
]
```

## After a wake

On every wake, the agent reconnects to its stored servers in the background. So a tool can be missing for a moment after a wake, and `call_tool` raises `MCPServerNotReadyError` until its server is ready.

To wait for them, call `await self.mcp.wait_for_connections(timeout=5)`. It never raises. On an `AIChatAgent`, set `AIChatAgentOptions(mcp=MCPOptions(), wait_for_mcp_connections=5)` to wait before each turn. `True` waits as long as it takes.

## Tell your clients

Connected clients get a `cf_agent_mcp_servers` frame on connect and after each change, with every server, tool, prompt and resource. `self.get_mcp_servers()` returns the same data. With MCP off, clients still get the frame, with nothing in it, so they keep working.

## `MCPOptions`

| Field                    | Default | Description |
| ------------------------ | ------- | ----------- |
| `client_name`            | The agent's class name | Sent to servers as the client's name. |
| `client_version`         | The `cf-agents` version | Sent to servers as the client's version. |
| `discovery_timeout`      | `15`    | How long loading a server's catalog can take after it connects. |
| `oauth_success_redirect` | `None`  | Where the callback redirects on success, resolved against the request's origin. |
| `oauth_error_redirect`   | `None`  | Where it redirects on failure. |

## Errors

Everything here subclasses `MCPError`, which subclasses `AgentsException`.

| Exception                       | Raised when |
| ------------------------------- | ----------- |
| `MCPServerIdConflictError`      | The id belongs to a different name or URL. |
| `MCPBlockedUrlError`            | The URL points at a private or metadata address. |
| `MCPConnectionError`            | The server couldn't be connected. It stays stored. |
| `MCPServerNotFoundError`        | No server with this id has a live connection. A server that's stored but closed raises this too. |
| `MCPServerNotReadyError`        | The call needs a server that isn't ready. |
| `MCPAuthorizationRequiredError` | The server needs OAuth, and the user has to visit `auth_url` first. |
| `MCPOAuthError`                 | An OAuth step failed. |
| `MCPRpcError`                   | The server answered with a JSON-RPC error. |
| `MCPTransportError`             | The HTTP request failed. |
| `MCPProtocolError`              | The server broke the protocol, or needs something the client lacks. |

`MCPNotEnabledError` is the exception for MCP being off, and imports from `agents`. Everything else imports from `agents.mcp`.
