import asyncio
import sys
from pathlib import Path

import pydantic_ai
from fake_runtime import Host

from agents.harness import tool
from agents.harness.pydantic_ai import PydanticAIHarness, WorkersAIModel
from agents.lifecycle import Lifecycle

sys.path.insert(0, str(Path(__file__).parents[1] / "models"))
from test_workers_ai import FakeAI  # noqa: E402

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
