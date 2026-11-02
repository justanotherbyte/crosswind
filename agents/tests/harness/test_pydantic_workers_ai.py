import asyncio
import sys
from pathlib import Path

import pydantic_ai
import pytest
from fake_runtime import Host
from workers import Response

from agents.harness import OperationEvent, TextEvent, ToolCallEvent, tool
from agents.harness.pydantic_ai import PydanticAIHarness, WorkersAIModel, workers_ai
from agents.lifecycle import Lifecycle

sys.path.insert(0, str(Path(__file__).parents[1] / "models"))
from test_workers_ai import SEARCH_STREAM, FakeAI, chunk, sse  # noqa: E402

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
SEARCH = {
    "id": "c1",
    "type": "function",
    "function": {"name": "search", "arguments": '{"query":"milk"}'},
}


async def search(query: str) -> str:
    """Search the notes."""
    return f"found {query}"


class Notes:
    @tool(replay="safe")
    async def search(self, query: str) -> str:
        """Search the notes."""
        return f"found {query}"


def test_a_tool_round_trip_through_the_harness() -> None:
    ai = FakeAI(
        sse(*SEARCH_STREAM, usage=(10, 4)),
        sse(
            chunk({"content": "You have"}), chunk({"content": " milk."}), usage=(20, 3)
        ),
    )
    model = WorkersAIModel("@cf/model", binding=ai, settings={"max_tokens": 50})
    agent = pydantic_ai.Agent(model, instructions="Be brief.")
    harness = PydanticAIHarness("notes", agent, target=Notes())
    Lifecycle(Host()).use(harness)

    async def scenario() -> list[object]:
        events = harness.session().watch()
        await anext(events)
        result = await harness.session().prompt("milk?", operation_id="op")
        assert result.text == "You have milk."
        seen = []
        while not isinstance(
            event := await anext(events), OperationEvent
        ) or event.status not in ("done", "failed"):
            seen.append(event)
        await events.aclose()
        return seen

    seen = asyncio.run(scenario())

    assert (
        ToolCallEvent(
            operation_id="op", tool_call_id="c1", name="search", args={"query": "milk"}
        )
        in seen
    )
    assert [event for event in seen if isinstance(event, TextEvent)] == [
        TextEvent(operation_id="op", delta="You have"),
        TextEvent(operation_id="op", delta=" milk."),
    ]
    first, second = (payload for _, payload in ai.calls)
    assert first["stream"] is True
    assert first["max_tokens"] == 50
    *opening, instructions, prompt = first["messages"]
    assert all(message["role"] == "system" for message in opening)
    assert instructions == {"role": "system", "content": "Be brief."}
    assert prompt == {"role": "user", "content": "milk?"}
    assert first["tools"][0]["function"]["name"] == "search"
    assert second["messages"][-2:] == [
        {"role": "assistant", "content": "", "tool_calls": [SEARCH]},
        {
            "role": "tool",
            "name": "search",
            "tool_call_id": "c1",
            "content": "found milk",
        },
    ]


def test_reasoning_becomes_a_thinking_part() -> None:
    agent = pydantic_ai.Agent(WorkersAIModel("@cf/model", binding=FakeAI(QWEN)))

    reply = asyncio.run(agent.run("weather?")).all_messages()[-1]

    assert isinstance(reply, pydantic_ai.ModelResponse)
    thinking, text = reply.parts
    assert thinking == pydantic_ai.ThinkingPart(
        "\nThe user wants the weather.\n", provider_name="cloudflare"
    )
    assert text == pydantic_ai.TextPart("\n\nSunny.")


def test_a_streamed_answer_counts_the_requests_usage() -> None:
    ai = FakeAI(sse(chunk({"content": "Hi"}), usage=(36, 2)))
    agent = pydantic_ai.Agent(WorkersAIModel("@cf/model", binding=ai))

    async def run() -> pydantic_ai.RunUsage:
        async with agent.run_stream("hey") as stream:
            assert [text async for text in stream.stream_text(delta=True)] == ["Hi"]
        return stream.usage

    usage = asyncio.run(run())

    assert (usage.input_tokens, usage.output_tokens) == (36, 2)


def test_a_streamed_answer_can_be_cancelled() -> None:
    ai = FakeAI(sse(chunk({"content": "One"}), chunk({"content": " two"})))
    agent = pydantic_ai.Agent(WorkersAIModel("@cf/model", binding=ai))

    async def run() -> list[str]:
        async with agent.run_stream("count") as stream:
            seen = []
            async for text in stream.stream_text(delta=True, debounce_by=None):
                seen.append(text)
                await stream.cancel()
            return seen

    assert asyncio.run(run()) == ["One"]


def test_a_streamed_qwen_turn_keeps_reasoning_and_skips_blank_text() -> None:
    ai = FakeAI(
        sse(
            chunk({"reasoning_content": "Use search."}),
            chunk({"content": "\n\n"}),
            *SEARCH_STREAM[1:],
        ),
        sse(chunk({"content": "\n\n"}), chunk({"content": "You have milk."})),
    )
    agent = pydantic_ai.Agent(WorkersAIModel("@cf/model", binding=ai))
    harness = PydanticAIHarness("notes", agent, target=Notes())
    Lifecycle(Host()).use(harness)

    async def scenario() -> list[object]:
        events = harness.session().watch()
        await anext(events)
        await harness.session().prompt("milk?", operation_id="op")
        seen = []
        while not isinstance(
            event := await anext(events), OperationEvent
        ) or event.status not in ("done", "failed"):
            seen.append(event)
        await events.aclose()
        return seen

    seen = asyncio.run(scenario())

    assert [event for event in seen if isinstance(event, TextEvent)] == [
        TextEvent(operation_id="op", delta="You have milk.")
    ]
    tool_turn = ai.calls[1][1]["messages"][-2]
    assert tool_turn["content"] == ""
    assert tool_turn["tool_calls"][0]["function"]["name"] == "search"


def test_run_stream_skips_blank_text_before_a_tool_call() -> None:
    ai = FakeAI(
        sse(
            chunk({"reasoning_content": "Use search."}),
            chunk({"content": "\n\n"}),
            *SEARCH_STREAM[1:],
        ),
        sse(chunk({"content": "You have milk."})),
    )
    agent = pydantic_ai.Agent(WorkersAIModel("@cf/model", binding=ai), tools=[search])

    async def run() -> tuple[str, list[pydantic_ai.ModelMessage]]:
        async with agent.run_stream("milk?") as stream:
            return await stream.get_output(), stream.all_messages()

    output, messages = asyncio.run(run())

    assert output == "You have milk."
    first = messages[1]
    assert isinstance(first, pydantic_ai.ModelResponse)
    assert first.parts[0] == pydantic_ai.ThinkingPart(
        "Use search.", provider_name="cloudflare"
    )


def test_request_still_answers_without_streaming() -> None:
    ai = FakeAI({"response": "Hi"})
    agent = pydantic_ai.Agent(WorkersAIModel("@cf/model", binding=ai))

    assert asyncio.run(agent.run("hey")).output == "Hi"
    assert "stream" not in ai.calls[0][1]


def test_streamed_blank_lines_skip_events_output_and_history() -> None:
    ai = FakeAI(
        sse(
            chunk({"reasoning_content": "The user wants the weather."}),
            chunk({"content": "\n\n"}),
            chunk({"content": "Sunny."}),
        ),
        sse(chunk({"content": "Still sunny."})),
    )
    harness = PydanticAIHarness(
        "notes",
        pydantic_ai.Agent(WorkersAIModel("@cf/model", binding=ai)),
        target=Notes(),
    )
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


PNG = b"\x89PNG\r\n\x1a\n"
PNG_URL = "data:image/png;base64,iVBORw0KGgo="


def _image(url: str) -> dict[str, object]:
    return {"type": "image_url", "image_url": {"url": url}}


def _run(prompt: object, *outputs: dict[str, object]) -> FakeAI:
    ai = FakeAI(*outputs or ({"response": "ok"},))
    agent = pydantic_ai.Agent(WorkersAIModel("@cf/model", binding=ai))
    asyncio.run(agent.run(prompt))
    return ai


def test_an_image_prompt_is_sent_as_text_and_image_parts() -> None:
    ai = _run(["What is this?", pydantic_ai.BinaryImage(PNG, media_type="image/png")])

    assert ai.calls[0][1]["messages"][-1] == {
        "role": "user",
        "content": [{"type": "text", "text": "What is this?"}, _image(PNG_URL)],
    }


def test_a_text_only_list_prompt_is_sent_as_one_string() -> None:
    ai = _run(["one", pydantic_ai.TextContent("two")])

    assert ai.calls[0][1]["messages"][-1] == {"role": "user", "content": "one\ntwo"}


def test_an_image_url_is_downloaded_and_sent_as_a_data_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetched: list[str] = []

    async def fetch(url: str) -> Response:
        fetched.append(url)
        return Response(PNG, headers={"content-type": "image/png; charset=binary"})

    monkeypatch.setattr(workers_ai, "fetch", fetch)
    ai = _run([pydantic_ai.ImageUrl("https://example.com/cat")])

    assert fetched == ["https://example.com/cat"]
    assert ai.calls[0][1]["messages"][-1]["content"] == [_image(PNG_URL)]


def test_a_failed_image_download_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fetch(url: str) -> Response:
        return Response(b"", status=404)

    monkeypatch.setattr(workers_ai, "fetch", fetch)

    with pytest.raises(ValueError, match="HTTP 404"):
        _run([pydantic_ai.ImageUrl("https://example.com/cat.png")])


@pytest.mark.parametrize(
    "item",
    [
        pydantic_ai.DocumentUrl("https://example.com/a.pdf"),
        pydantic_ai.BinaryContent(b"%PDF", media_type="application/pdf"),
    ],
)
def test_files_other_than_images_are_rejected(item: object) -> None:
    with pytest.raises(TypeError, match="application/pdf"):
        _run(["read this", item])


def test_a_tools_images_follow_the_tool_results() -> None:
    ai = FakeAI(
        {"response": "", "tool_calls": [{"id": "c1", "name": "snap", "arguments": {}}]},
        {"response": "A cat."},
    )
    agent = pydantic_ai.Agent(WorkersAIModel("@cf/model", binding=ai))

    @agent.tool_plain
    def snap() -> pydantic_ai.BinaryImage:
        """Take a photo."""
        return pydantic_ai.BinaryImage(PNG, media_type="image/png", identifier="f1")

    asyncio.run(agent.run("What do you see?"))

    tool, files = ai.calls[1][1]["messages"][-2:]
    assert tool["role"] == "tool"
    assert tool["content"] == "See file f1."
    assert files["role"] == "user"
    assert _image(PNG_URL) in files["content"]


def test_a_tools_documents_reach_the_model_as_a_note() -> None:
    ai = FakeAI(
        {"response": "", "tool_calls": [{"id": "c1", "name": "read", "arguments": {}}]},
        {"response": "Done."},
    )
    agent = pydantic_ai.Agent(WorkersAIModel("@cf/model", binding=ai))

    @agent.tool_plain
    def read() -> pydantic_ai.BinaryContent:
        """Read the report."""
        return pydantic_ai.BinaryContent(b"%PDF", media_type="application/pdf")

    asyncio.run(agent.run("Summarize the report."))

    tool = ai.calls[1][1]["messages"][-1]
    assert tool["role"] == "tool"
    assert "cannot read documents" in tool["content"]
