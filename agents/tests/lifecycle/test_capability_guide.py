"""The sample in docs/docs/agents/custom-capability.md, and the rules it states."""

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

import pytest
from fake_runtime import FakeCtx
from workers import Request, Response

from agents import (
    Agent,
    JobContext,
    JobOutcome,
    LifecycleCapability,
    get_current_agent,
)


async def fetch(url: str, **_: Any) -> Any:
    raise NotImplementedError  # replaced per test, as `workers.fetch` in the guide


class Outbox(LifecycleCapability):
    def __init__(self) -> None:
        super().__init__("outbox")

    async def on_start(self) -> None:
        self.lifecycle.sql(
            """CREATE TABLE IF NOT EXISTS outbox_failed (
                 id TEXT PRIMARY KEY, url TEXT NOT NULL, error TEXT NOT NULL
               )"""
        )

    async def send(self, url: str, body: str) -> str:
        job = await self.lifecycle.jobs.push(
            fn="deliver", time=datetime.now(UTC), payload={"url": url, "body": body}
        )
        return job.id

    async def on_job(self, context: JobContext) -> JobOutcome:
        payload = context.job.payload
        response = await fetch(payload["url"], method="POST", body=payload["body"])
        if not response.ok:
            raise RuntimeError(f"{payload['url']} answered {response.status}")
        return None

    async def on_job_error(self, context: JobContext, error: Exception) -> JobOutcome:
        self.lifecycle.sql(
            "INSERT OR REPLACE INTO outbox_failed VALUES (?, ?, ?)",
            context.job.id,
            context.job.payload["url"],
            str(error),
        )
        return None

    async def on_request(self, request: Request) -> Response | None:
        if not urlsplit(request.url).path.endswith("/outbox/failed"):
            return None
        rows = self.lifecycle.sql("SELECT * FROM outbox_failed")
        return Response(json.dumps(list(rows)))


class Orders(Agent):
    def __init__(self, ctx: Any, env: Any) -> None:
        super().__init__(ctx, env)
        self.outbox = self.use(Outbox())

    async def on_request(self, request: Request) -> Response:
        await self.outbox.send("https://example.com/hooks", await request.text())
        return Response("queued")


def answering(calls: list[str], *, ok: bool) -> Any:
    async def fake(url: str, **_: Any) -> Any:
        calls.append(url)
        return SimpleNamespace(ok=ok, status=200 if ok else 503)

    return fake


URL = "https://example.com/agents/orders/a"


def test_a_delivered_job_is_deleted(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(f"{__name__}.fetch", answering(calls, ok=True))

    async def main() -> None:
        agent = Orders(FakeCtx("a"), env=None)
        await agent.lifecycle.start()
        await agent.outbox.send("https://example.com/hooks", "1")
        await agent.alarm()  # ty: ignore[unresolved-attribute]

        assert calls == ["https://example.com/hooks"]
        assert agent.outbox.lifecycle.jobs.list() == []

    asyncio.run(main())


def test_a_failing_job_retries_three_times_then_reaches_on_job_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(f"{__name__}.fetch", answering(calls, ok=False))

    async def main() -> None:
        agent = Orders(FakeCtx("a"), env=None)
        await agent.lifecycle.start()
        job_id = await agent.outbox.send("https://example.com/hooks", "1")
        await agent.alarm()  # ty: ignore[unresolved-attribute]

        assert len(calls) == 3
        assert agent.outbox.lifecycle.jobs.list() == []
        response = await agent.fetch(  # ty: ignore[unresolved-attribute]
            Request(f"{URL}/outbox/failed")
        )
        assert '"error"' in response.body and job_id in response.body

    asyncio.run(main())


def test_a_capability_answers_before_the_agents_on_request() -> None:
    async def main() -> None:
        agent = Orders(FakeCtx("a"), env=None)
        claimed = await agent.fetch(  # ty: ignore[unresolved-attribute]
            Request(f"{URL}/outbox/failed")
        )
        assert claimed.body == "[]"

    asyncio.run(main())


def test_a_job_pushed_on_the_agents_own_lifecycle_is_dropped() -> None:
    async def main() -> None:
        agent = Orders(FakeCtx("a"), env=None)
        await agent.lifecycle.start()
        await agent.lifecycle.jobs.push(fn="lost", time=datetime.now(UTC))
        await agent.alarm()  # ty: ignore[unresolved-attribute]

        assert agent.lifecycle.jobs.list() == []

    asyncio.run(main())


@pytest.mark.parametrize(
    "taken",
    [
        "dynamic-agents",
        "scheduler",
        "queue",
        "state",
        "websockets",
        "tasks",
        "keep-alive",
        "fibers",
    ],
)
def test_built_in_ids_are_taken_on_an_agent(taken: str) -> None:
    agent = Agent(FakeCtx("a"), env=None)
    with pytest.raises(RuntimeError, match="already installed"):
        agent.use(LifecycleCapability(taken))


def test_install_after_startup_raises() -> None:
    async def main() -> None:
        agent = Agent(FakeCtx("a"), env=None)
        await agent.lifecycle.start()
        with pytest.raises(RuntimeError, match="before startup"):
            agent.use(Outbox())

    asyncio.run(main())


def test_hooks_run_outside_the_host_context_until_asked() -> None:
    seen: list[Any] = []

    class Probe(LifecycleCapability):
        async def on_request(self, request: Request) -> Response | None:
            seen.append(get_current_agent())

            async def inside() -> None:
                seen.append(get_current_agent())

            await self.lifecycle.run_in_host_context(inside, request=request)
            return Response("ok")

    class Host(Agent):
        def __init__(self, ctx: Any, env: Any) -> None:
            super().__init__(ctx, env)
            self.use(Probe("probe"))

    async def main() -> None:
        agent = Host(FakeCtx("a"), env=None)
        await agent.fetch(Request(URL))  # ty: ignore[unresolved-attribute]

        assert seen[0] is None
        assert seen[1].agent is agent

    asyncio.run(main())


def test_a_failed_on_start_is_retried_by_the_next_event() -> None:
    class Flaky(LifecycleCapability):
        attempts = 0

        async def on_start(self) -> None:
            Flaky.attempts += 1
            if Flaky.attempts == 1:
                raise RuntimeError("boom")

    class Host(Agent):
        def __init__(self, ctx: Any, env: Any) -> None:
            super().__init__(ctx, env)
            self.use(Flaky("flaky"))

    async def main() -> None:
        agent = Host(FakeCtx("a"), env=None)
        failed = await agent.fetch(Request(URL))  # ty: ignore[unresolved-attribute]
        assert failed.status == 500

        await agent.lifecycle.start()
        assert Flaky.attempts == 2
        assert agent.lifecycle.is_started()

    asyncio.run(main())
