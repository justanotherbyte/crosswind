import asyncio
from typing import Any

import pytest

from agents.models import Completion, ToolCall, Usage, WorkersAIModel


class FakeAI:
    """The ``ai`` binding: records each payload and returns canned outputs."""

    def __init__(self, *outputs: dict[str, Any]) -> None:
        self.outputs = list(outputs)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def run(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, payload))
        return self.outputs.pop(0)


def test_complete_sends_messages_tools_and_options() -> None:
    ai = FakeAI({"response": "hi"})
    model = WorkersAIModel("@cf/model", binding=ai)
    tool = {
        "type": "function",
        "function": {"name": "f", "description": "", "parameters": {}},
    }

    completion = asyncio.run(
        model.complete(
            [{"role": "user", "content": "hey"}],
            tools=[tool],
            options={"max_tokens": 5},
        )
    )

    assert completion == Completion(text="hi")
    assert ai.calls == [
        (
            "@cf/model",
            {
                "max_tokens": 5,
                "messages": [{"role": "user", "content": "hey"}],
                "tools": [tool],
            },
        )
    ]


def test_parses_flat_tool_calls_and_generates_missing_ids() -> None:
    completion = Completion.from_output(
        {
            "response": "",
            "tool_calls": [{"name": "search", "arguments": {"q": "x"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        }
    )

    [call] = completion.tool_calls
    assert (call.name, call.arguments) == ("search", {"q": "x"})
    assert call.id.startswith("call_")
    assert completion.usage == Usage(input_tokens=3, output_tokens=2)


def test_parses_openai_style_choices() -> None:
    completion = Completion.from_output(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "ok",
                        "tool_calls": [
                            {
                                "id": "c1",
                                "type": "function",
                                "function": {"name": "f", "arguments": '{"a": 1}'},
                            }
                        ],
                    }
                }
            ]
        }
    )

    assert completion == Completion(
        text="ok", tool_calls=(ToolCall(id="c1", name="f", arguments={"a": 1}),)
    )


def test_json_mode_response_becomes_text() -> None:
    assert Completion.from_output({"response": {"a": 1}}).text == '{"a": 1}'


def test_complete_needs_a_message() -> None:
    with pytest.raises(ValueError, match="at least one message"):
        asyncio.run(WorkersAIModel("m", binding=FakeAI()).complete([]))


def test_clean_text_rewrites_the_answer() -> None:
    model = WorkersAIModel(
        "m", binding=FakeAI({"response": "\n\nSunny."}), clean_text=str.strip
    )

    completion = asyncio.run(model.complete([{"role": "user", "content": "hey"}]))

    assert completion.text == "Sunny."
