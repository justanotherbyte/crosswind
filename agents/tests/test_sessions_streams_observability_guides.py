"""The samples in docs/docs/agents/sessions-and-streams.md and observability.md."""

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from fake_runtime import FakeCtx

from agents import Agent, ObservabilityEvent, Sessions, Streams, callable, subscribe
from agents.core.encoding import to_json


class Reports(Agent):
    def __init__(self, ctx: Any, env: Any) -> None:
        super().__init__(ctx, env)
        self.streams = self.use(Streams())

    @callable
    async def write(self, report_id: str) -> None:
        writer = await self.streams.open(report_id, tag="report")
        for section in ("summary", "details"):
            writer.append({"section": section})
        writer.close()

    @callable
    async def read(self, report_id: str, start: int = 0) -> list[dict[str, Any]]:
        return [
            {"seq": chunk.seq, "chunk": chunk.chunk}
            async for chunk in self.streams.read(report_id, start=start)
        ]


def test_streams_sample() -> None:
    async def main() -> None:
        agent = Reports(FakeCtx("a"), env=None)
        await agent.lifecycle.start()
        await agent.write("r1")

        assert await agent.read("r1") == [
            {"seq": 0, "chunk": {"section": "summary"}},
            {"seq": 1, "chunk": {"section": "details"}},
        ]
        assert await agent.read("r1", start=1) == [
            {"seq": 1, "chunk": {"section": "details"}}
        ]
        status = await agent.streams.status("r1")
        assert status is not None
        assert (status.state, status.cursor, status.tag) == ("completed", 2, "report")

    asyncio.run(main())


def test_a_reader_replays_then_follows_a_live_stream() -> None:
    async def main() -> None:
        agent = Reports(FakeCtx("a"), env=None)
        await agent.lifecycle.start()
        writer = await agent.streams.open("live")
        writer.append("one")
        reader = asyncio.ensure_future(agent.read("live"))
        await asyncio.sleep(0)
        writer.append("two")
        writer.close()

        assert [item["chunk"] for item in await reader] == ["one", "two"]

    asyncio.run(main())


class Conversations(Agent):
    def __init__(self, ctx: Any, env: Any) -> None:
        super().__init__(ctx, env)
        self.sessions = self.use(Sessions())

    @callable
    async def add(self, user_id: str, message_id: str, text: str) -> bool:
        result = await self.sessions.session(user_id).append_message(
            {
                "id": message_id,
                "role": "user",
                "parts": [{"type": "text", "text": text}],
            }
        )
        return result.inserted

    @callable
    async def recent(self, user_id: str) -> list[str]:
        recent = await self.sessions.session(user_id).get_recent_history(16_000)
        return [message["id"] for message in recent.messages]


def test_sessions_sample() -> None:
    async def main() -> None:
        agent = Conversations(FakeCtx("a"), env=None)
        await agent.lifecycle.start()

        assert await agent.add("ada", "m1", "hello") is True
        assert await agent.add("ada", "m1", "hello again") is False
        assert await agent.add("ada", "m2", "bye") is True
        assert await agent.add("grace", "m1", "hi") is True
        assert await agent.recent("ada") == ["m1", "m2"]
        assert await agent.recent("grace") == ["m1"]

    asyncio.run(main())


class ErrorsOnly:
    def emit(self, event: ObservabilityEvent) -> None:
        if event.type.endswith(":error"):
            print(event.type, event.agent, event.name, event.payload)


class Watched(Agent):
    observability = ErrorsOnly()


def test_a_custom_sink_receives_events(capsys: pytest.CaptureFixture[str]) -> None:
    async def main() -> None:
        agent = Watched(FakeCtx("a"), env=None)
        await agent.lifecycle.start()
        agent._emit("rpc", {"method": "x"})
        agent._emit("queue:error", {"id": "1"})

    asyncio.run(main())
    assert capsys.readouterr().out == "queue:error Watched a {'id': '1'}\n"


def test_an_event_serializes_with_a_millisecond_timestamp() -> None:
    event = ObservabilityEvent(
        type="rpc",
        agent="Counter",
        name="my-counter",
        payload={"method": "increment"},
        timestamp=datetime(2026, 11, 3, tzinfo=UTC),
    )
    assert json.loads(to_json(event)) == {
        "type": "rpc",
        "agent": "Counter",
        "name": "my-counter",
        "payload": {"method": "increment"},
        "timestamp": 1793664000000,
    }


def test_subscribe_sees_the_default_sinks_events() -> None:
    seen: list[str] = []

    class Plain(Agent):
        initial_state = {"count": 0}  # noqa: RUF012

    async def main() -> None:
        agent = Plain(FakeCtx("a"), env=None)
        await agent.lifecycle.start()
        handle = subscribe("state", lambda event: seen.append(event.type))
        agent.set_state({"count": 1})
        handle.dispose()
        agent.set_state({"count": 2})
        assert seen == ["state:update"]

    asyncio.run(main())
