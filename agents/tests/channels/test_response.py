import asyncio
import json
from pathlib import Path

import pytest
from fake_runtime import Host

from agents.channels import ResponseGrammarError, ResponseWriter, TextStart
from agents.harness.transcript import chunk_from_wire, to_wire
from agents.lifecycle import Lifecycle
from agents.streams import StreamClosedError, Streams, StreamSerializationError

GRAMMAR = json.loads(
    (Path(__file__).parent / "golden" / "web_frames.json").read_text()
)["grammar"]


def run(scenario, **options):
    streams = Streams(**options)
    Lifecycle(Host()).use(streams)

    async def main():
        await streams.lifecycle.ready()
        return await scenario(streams)

    return asyncio.run(main())


async def chunks(streams, stream_id):
    return [c.chunk async for c in streams.read(stream_id)]


def append_all(response, sequence):
    # What Channels does with harness output: a chunk that can't be recorded
    # is dropped, and the response goes on.
    rejected = []
    for chunk in sequence:
        try:
            response.append(chunk)
        except (ResponseGrammarError, StreamSerializationError) as error:
            rejected.append(error)
    return rejected


def test_upstream_grammar_and_size_case():
    async def scenario(streams):
        response = ResponseWriter(await streams.open("r1"))
        rejected = append_all(response, map(chunk_from_wire, GRAMMAR["chunks"]))
        response.end()
        return rejected, await chunks(streams, "r1")

    rejected, recorded = run(scenario, max_chunk_bytes=GRAMMAR["maxChunkBytes"])
    assert recorded == GRAMMAR["recorded"]
    assert [str(e) for e in rejected if isinstance(e, ResponseGrammarError)] == [
        'text "a" is not open',
        'text "a" is already open',
        'text "a" is not open',
    ]
    assert sum(isinstance(e, StreamSerializationError) for e in rejected) == 1


def text(kind, step, id="a"):
    chunk = {"type": f"{kind}-{step}", "id": id}
    if step == "delta":
        chunk["delta"] = "x"
    return chunk_from_wire(chunk)


def tool(step, id="c"):
    chunk = {"type": f"tool-input-{step}", "toolCallId": id}
    if step in ("start", "available", "error"):
        chunk["toolName"] = "search"
    if step == "delta":
        chunk["delta"] = "{"
    if step in ("available", "error"):
        chunk["input"] = {}
    if step == "error":
        chunk["errorText"] = "bad"
    return chunk_from_wire(chunk)


# (name, chunks before, the chunk, the error it raises or None)
GRAMMAR_TABLE = [
    ("text start", [], text("text", "start"), None),
    ("text delta, open", [text("text", "start")], text("text", "delta"), None),
    ("text end, open", [text("text", "start")], text("text", "end"), None),
    ("text delta, never opened", [], text("text", "delta"), 'text "a" is not open'),
    ("text end, never opened", [], text("text", "end"), 'text "a" is not open'),
    (
        "text start, already open",
        [text("text", "start")],
        text("text", "start"),
        'text "a" is already open',
    ),
    (
        "text delta, after end",
        [text("text", "start"), text("text", "end")],
        text("text", "delta"),
        'text "a" is not open',
    ),
    (
        "text start, id reused after end",
        [text("text", "start"), text("text", "end")],
        text("text", "start"),
        None,
    ),
    ("text start, another id", [text("text", "start")], text("text", "start", "b"), None),
    (
        "reasoning ids are separate from text ids",
        [text("text", "start")],
        text("reasoning", "start"),
        None,
    ),
    (
        "reasoning delta, text open",
        [text("text", "start")],
        text("reasoning", "delta"),
        'reasoning "a" is not open',
    ),
    (
        "reasoning start, already open",
        [text("reasoning", "start")],
        text("reasoning", "start"),
        'reasoning "a" is already open',
    ),
    ("reasoning end, open", [text("reasoning", "start")], text("reasoning", "end"), None),
    ("tool input start", [], tool("start"), None),
    ("tool input delta, started", [tool("start")], tool("delta"), None),
    ("tool input delta, never started", [], tool("delta"), 'tool input "c" is not open'),
    (
        "tool input start, already started",
        [tool("start")],
        tool("start"),
        'tool input "c" is already open',
    ),
    ("tool input available, never started", [], tool("available"), None),
    ("tool input error, never started", [], tool("error"), None),
    ("tool input available, started", [tool("start")], tool("available"), None),
    (
        "tool input delta, after available",
        [tool("start"), tool("available")],
        tool("delta"),
        'tool input "c" is not open',
    ),
    (
        "tool input start, after error",
        [tool("start"), tool("error")],
        tool("start"),
        None,
    ),
    (
        "tool input ids are separate from text ids",
        [text("text", "start", "c")],
        tool("delta"),
        'tool input "c" is not open',
    ),
    (
        "tool output, input never seen",
        [],
        chunk_from_wire({"type": "tool-output-available", "toolCallId": "c", "output": 1}),
        None,
    ),
    ("approval request", [], chunk_from_wire({"type": "tool-approval-request", "toolCallId": "c", "approvalId": "a"}), None),
    ("tool output denied", [], chunk_from_wire({"type": "tool-output-denied", "toolCallId": "c"}), None),
    ("tool output error", [], chunk_from_wire({"type": "tool-output-error", "toolCallId": "c", "errorText": "x"}), None),
    ("file", [], chunk_from_wire({"type": "file", "mediaType": "text/plain", "url": "data:,x"}), None),
    ("source url", [], chunk_from_wire({"type": "source-url", "sourceId": "s", "url": "https://x"}), None),
    ("data", [], chunk_from_wire({"type": "data", "name": "n", "data": None}), None),
    ("metadata", [], chunk_from_wire({"type": "metadata", "metadata": {"k": 1}}), None),
]


@pytest.mark.parametrize(
    ("before", "chunk", "error"),
    [case[1:] for case in GRAMMAR_TABLE],
    ids=[case[0] for case in GRAMMAR_TABLE],
)
def test_grammar(before, chunk, error):
    async def scenario(streams):
        response = ResponseWriter(await streams.open("r"))
        for earlier in before:
            response.append(earlier)
        if error is None:
            response.append(chunk)
        else:
            with pytest.raises(ResponseGrammarError, match=error):
                response.append(chunk)
        response.end()
        return await chunks(streams, "r")

    recorded = run(scenario)
    expected = [*before, chunk] if error is None else before
    assert recorded == [to_wire(c) for c in expected]


def test_a_chunk_that_fails_to_record_changes_no_part():
    async def scenario(streams):
        response = ResponseWriter(await streams.open("r"))
        big = TextStart(id="a", provider_metadata={"x": "y" * 64})
        with pytest.raises(StreamSerializationError):
            response.append(big)
        # The start was not recorded, so the part is still free and closed.
        with pytest.raises(ResponseGrammarError, match="is not open"):
            response.append(text("text", "delta"))
        response.append(text("text", "start"))
        response.end()
        return await chunks(streams, "r")

    assert run(scenario, max_chunk_bytes=64) == [to_wire(text("text", "start"))]


def test_id_and_end():
    async def scenario(streams):
        response = ResponseWriter(await streams.open("r1"))
        response.append(text("text", "start"))
        response.end()
        with pytest.raises(StreamClosedError):
            response.append(text("text", "delta"))
        status = await streams.status("r1")
        return response.id, status.state

    # Parts still open at the end are closed by ending the response.
    assert run(scenario) == ("r1", "completed")


def test_pipe_appends_then_ends():
    async def source():
        yield text("text", "start")
        yield text("text", "delta")

    async def scenario(streams):
        response = ResponseWriter(await streams.open("r"))
        await response.pipe(source())
        return (await streams.status("r")).state, await chunks(streams, "r")

    recorded = [to_wire(text("text", "start")), to_wire(text("text", "delta"))]
    assert run(scenario) == ("completed", recorded)


@pytest.mark.parametrize("failure", ["source", "grammar"])
def test_pipe_interrupts_the_response_on_failure(failure):
    async def source():
        yield text("text", "start")
        if failure == "source":
            raise RuntimeError("model down")
        yield text("text", "start")

    async def scenario(streams):
        response = ResponseWriter(await streams.open("r"))
        expected = RuntimeError if failure == "source" else ResponseGrammarError
        with pytest.raises(expected):
            await response.pipe(source())
        status = await streams.status("r")
        return status.state, status.error, await chunks(streams, "r")

    recorded = [to_wire(text("text", "start"))]
    assert run(scenario) == ("errored", "interrupted", recorded)


def test_pipe_interrupts_the_response_when_cancelled():
    async def scenario(streams):
        response = ResponseWriter(await streams.open("r"))
        started = asyncio.Event()

        async def source():
            yield text("text", "start")
            started.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(response.pipe(source()))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        status = await streams.status("r")
        return status.state, status.error

    assert run(scenario) == ("errored", "interrupted")
