import asyncio
import sys
from pathlib import Path

import pydantic_ai
from fake_runtime import Host

from agents.harness import OperationEvent, TextEvent, tool
from agents.harness.pydantic_ai import PydanticAIHarness, WorkersAIModel
from agents.lifecycle import Lifecycle

sys.path.insert(0, str(Path(__file__).parents[1] / "models"))
from test_workers_ai import FakeAI  # noqa: E402

QWEN = {
    "choices": [
        {
            "message": {
                "role": "assistant",
                "content": "\n\nSunny.",
                "reasoning_content": "\nThe user wants the weather.\n",
            }
        }
    ]
}
SEARCH = {"id": "c1", "type": "function", "function": {"name": "search", "arguments": '{"query":"milk"}'}}


class Notes:
    @tool(replay="safe")
    async def search(self, query: str) -> str:
        """Search the notes."""
        return f"found {query}"


def test_a_tool_round_trip_through_the_harness() -> None:
    ai = FakeAI(
        {"response": "", "tool_calls": [SEARCH]},
        {"response": "You have milk."},
    )
    model = WorkersAIModel("@cf/model", binding=ai, settings={"max_tokens": 50})
    agent = pydantic_ai.Agent(model, instructions="Be brief.")
    harness = PydanticAIHarness("notes", agent, target=Notes())
    Lifecycle(Host()).use(harness)

    result = asyncio.run(harness.prompt("milk?"))

    assert result.text == "You have milk."
    first, second = (payload for _, payload in ai.calls)
    assert first["max_tokens"] == 50
    *opening, instructions, prompt = first["messages"]
    assert all(message["role"] == "system" for message in opening)
    assert instructions == {"role": "system", "content": "Be brief."}
    assert prompt == {"role": "user", "content": "milk?"}
    assert first["tools"][0]["function"]["name"] == "search"
    assert second["messages"][-2:] == [
        {"role": "assistant", "content": "", "tool_calls": [SEARCH]},
        {"role": "tool", "name": "search", "tool_call_id": "c1", "content": "found milk"},
    ]


def test_non_text_prompts_are_rejected() -> None:
    agent = pydantic_ai.Agent(WorkersAIModel("@cf/model", binding=FakeAI()))
    image = pydantic_ai.ImageUrl("https://example.com/a.png")

    try:
        asyncio.run(agent.run(["look", image]))
    except TypeError as error:
        assert "text prompts" in str(error)
    else:
        raise AssertionError("expected TypeError")


def test_clean_text_reaches_events_output_and_history() -> None:
    ai = FakeAI(QWEN, {"response": "Still sunny."})
    model = WorkersAIModel("@cf/model", binding=ai, clean_text=str.strip)
    harness = PydanticAIHarness("notes", pydantic_ai.Agent(model), target=Notes())
    Lifecycle(Host()).use(harness)

    async def scenario() -> list[object]:
        events = harness.session().watch()
        await anext(events)

        result = await harness.session().prompt("weather?", operation_id="op")
        seen = []
        async for event in events:
            seen.append(event)
            if isinstance(event, OperationEvent) and event.status == "done":
                break
        await events.aclose()
        await harness.session().prompt("and now?")

        assert result.text == "Sunny."
        return seen

    seen = asyncio.run(scenario())

    assert [event for event in seen if isinstance(event, TextEvent)] == [
        TextEvent(operation_id="op", delta="Sunny.")
    ]
    assert {"role": "assistant", "content": "Sunny."} in ai.calls[1][1]["messages"]
