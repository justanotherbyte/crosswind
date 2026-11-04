import asyncio
import uuid
from datetime import UTC, datetime

import pydantic_ai
from workers import Request, Response, WorkerEntrypoint

from agents import (
    Agent,
    OperationEvent,
    SessionSnapshot,
    callable,
    route_agent_request,
    tool,
)
from agents.harness.pydantic_ai import PydanticAIHarness, WorkersAIModel


class SelfHealing(Agent):
    initial_state = {"status": "idle", "crashes": 0, "trace": []}

    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        self.instance = uuid.uuid4().hex[:6]
        model = WorkersAIModel("@cf/qwen/qwen3-30b-a3b-fp8", binding=env.AI)
        agent = pydantic_ai.Agent(model, instructions="Use your tools, then report.")
        self.harness = self.use(PydanticAIHarness("healer", agent, target=self))

    @tool(replay="safe")
    async def migrate_database(self) -> str:
        """Run the database migration."""
        self.trace("tool", "migrate_database() started")
        if self.state["crashes"] == 0:
            await self.crash()
        await asyncio.sleep(5)  # Leaves time to press Crash
        self.trace("tool", "migrate_database() finished")
        return "Migration finished"

    @callable
    async def crash(self) -> None:
        crashes = self.state["crashes"] + 1
        self.trace("crash", "ctx.abort() reset this instance", crashes=crashes)
        await self.ctx.storage.sync()  # abort() drops unflushed writes
        self.ctx.abort("Crash in the middle of a turn")

    @callable
    async def reset(self) -> None:
        await self.harness.session().reset()
        self.set_state(type(self).initial_state)

    @callable
    async def run(self, prompt: str) -> None:
        session = self.harness.session()
        await self.reset()
        self.trace("run", prompt, status="running")
        await session.submit(prompt)

    async def on_start(self) -> None:
        self.follower = asyncio.create_task(self.follow_harness())

    async def follow_harness(self) -> None:
        session = self.harness.session()
        async for event in session.watch():
            if isinstance(event, SessionSnapshot) and event.operations:
                self.trace("wake", "Resumed the turn from its checkpoint")
            elif isinstance(event, OperationEvent) and event.status not in (
                "queued",
                "running",
            ):
                result = await session.wait(event.operation_id)
                text = result.text or result.reason or ""
                self.trace(result.status, text, status=result.status)

    def trace(self, kind: str, text: str, **state) -> None:
        at = datetime.now(UTC).isoformat()
        entry = {"at": at, "instance": self.instance, "kind": kind, "text": text}
        trace = [*self.state["trace"], entry]
        self.set_state({**self.state, **state, "trace": trace})


class Default(WorkerEntrypoint):
    async def fetch(self, request: Request) -> Response:
        response = await route_agent_request(request, self.env)
        return response or Response("Not found", status=404)
