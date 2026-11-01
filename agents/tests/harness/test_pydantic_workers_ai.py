import asyncio
import sys
from pathlib import Path

import pydantic_ai
import pytest
from fake_runtime import Host
from workers import Response

from agents.harness import tool
from agents.harness.pydantic_ai import PydanticAIHarness, WorkersAIModel, workers_ai
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
