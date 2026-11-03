"""The samples in docs/docs/agents/mcp-client.md and mcp-server.md."""

import asyncio

from _servers import URL, demo_server, serve
from fake_runtime import FakeCtx
from mcp.server import MCPServer
from workers import Request, Response

from agents import Agent, AgentOptions, MCPOptions, callable
from agents.mcp import TextContent
from agents.mcp.server import create_mcp_handler


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


def test_client_sample() -> None:
    async def main() -> None:
        async with serve(demo_server()) as transport:
            agent = Researcher(FakeCtx("a"), env=None)
            agent.mcp.http = transport
            await agent.lifecycle.start()

            assert await agent.connect(URL) is None
            assert "add" in await agent.tools()
            assert await agent.add(1, 2) == "3"

    asyncio.run(main())


def build_server() -> MCPServer:
    server = MCPServer("Notes", version="1.0.0")

    @server.tool()
    async def shout(text: str) -> str:
        """Return the text in capitals."""
        return text.upper()

    return server


handler = create_mcp_handler(build_server)


def test_server_sample_routes_only_its_path() -> None:
    async def main() -> Response:
        return await handler(Request("https://example.com/other"), None, None)

    assert asyncio.run(main()).status == 404
