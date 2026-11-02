import asyncio
import json
from typing import Any

import pytest

from agents.models import (
    Completion,
    ReasoningDelta,
    TextDelta,
    ToolCall,
    ToolCallDelta,
    Usage,
    WorkersAIModel,
)


class FakeAI:
    """The ``ai`` binding: records each payload and returns canned outputs.

    A streamed output is a list of SSE byte chunks (see `sse`).
    """

    def __init__(self, *outputs: dict[str, Any] | list[bytes]) -> None:
        self.outputs = list(outputs)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def run(self, name: str, payload: dict[str, Any]) -> Any:
        self.calls.append((name, payload))
        return self.outputs.pop(0)


def chunk(delta: dict[str, Any]) -> dict[str, Any]:
    """A streamed ``chat.completion.chunk`` carrying ``delta``."""
    return {
        "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 1},
        "response": delta.get("content"),
        "tool_calls": [],
    }


def sse(*events: dict[str, Any], usage: tuple[int, int] = (0, 0)) -> list[bytes]:
    """Encode chunks as Workers AI streams them, split across reads.

    The stream ends with the summary event carrying the request's ``usage``,
    then ``[DONE]``.
    """
    summary = {
        "response": "",
        "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]},
    }
    lines = [f"data: {json.dumps(event)}\n\n" for event in (*events, summary)]
    body = "".join([*lines, "data: [DONE]\n\n"]).encode()
    return [body[i : i + 7] for i in range(0, len(body), 7)]


SEARCH_STREAM = (
    chunk({"role": "assistant", "content": ""}),
    chunk(
        {
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "index": 0,
                    "function": {"name": "search"},
                }
            ]
        }
    ),
    chunk({"tool_calls": [{"index": 0, "function": {"arguments": '{"query":'}}]}),
    chunk({"tool_calls": [{"index": 0, "function": {"arguments": '"milk"}'}}]}),
)


async def collect(model: WorkersAIModel, stream: list[bytes]) -> list[Any]:
    """Every event `WorkersAIModel.stream` yields for ``stream``."""
    model.binding.outputs.append(stream)
    async with model.stream([{"role": "user", "content": "hey"}]) as events:
        return [event async for event in events]


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


def test_parses_reasoning_from_either_field() -> None:
    def message(**fields: str) -> dict[str, Any]:
        return {"choices": [{"message": {"content": "ok", **fields}}]}

    assert Completion.from_output(message(reasoning_content="r1")).reasoning == "r1"
    assert Completion.from_output(message(reasoning="r2")).reasoning == "r2"
    assert Completion.from_output(message()).reasoning == ""


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


def test_stream_sends_stream_true_and_yields_text_then_usage() -> None:
    model = WorkersAIModel("@cf/model", binding=FakeAI())
    stream = sse(
        chunk({"role": "assistant", "content": ""}),
        chunk({"content": "Hé"}),
        chunk({"reasoning_content": "hmm"}),
        chunk({"content": " there"}),
        usage=(36, 2),
    )

    events = asyncio.run(collect(model, stream))

    assert events == [
        TextDelta(text="Hé"),
        ReasoningDelta(text="hmm"),
        TextDelta(text=" there"),
        Usage(input_tokens=36, output_tokens=2),
    ]
    [(_, payload)] = model.binding.calls
    assert payload == {"messages": [{"role": "user", "content": "hey"}], "stream": True}


def test_stream_yields_tool_call_pieces() -> None:
    model = WorkersAIModel("@cf/model", binding=FakeAI())

    events = asyncio.run(collect(model, sse(*SEARCH_STREAM)))

    assert events[:-1] == [
        ToolCallDelta(index=0, id="c1", name="search"),
        ToolCallDelta(index=0, arguments='{"query":'),
        ToolCallDelta(index=0, arguments='"milk"}'),
    ]


def test_stream_reads_a_response_only_stream() -> None:
    model = WorkersAIModel("@cf/model", binding=FakeAI())
    stream = sse({"response": "Hi"}, {"response": "!"}, usage=(3, 2))

    events = asyncio.run(collect(model, stream))

    assert events == [
        TextDelta(text="Hi"),
        TextDelta(text="!"),
        Usage(input_tokens=3, output_tokens=2),
    ]


def test_stream_needs_a_message() -> None:
    async def first() -> None:
        async with WorkersAIModel("m", binding=FakeAI()).stream([]):
            pass

    with pytest.raises(ValueError, match="at least one message"):
        asyncio.run(first())
