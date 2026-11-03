"""Transcript records against upstream (cloudflare/agents@455b47e).

``golden/transcript_records.json`` holds every record in upstream's wire form,
written from ``experimental/channels/protocol.ts``. ``golden/apply_chunks.json``
holds upstream's ``apply-chunks.test.ts`` case plus more, with ``expected``
produced by upstream's ``applyChunks`` (``golden/tools/gen.mjs``).
"""

import json
from pathlib import Path

import pytest

from agents.harness.transcript import (
    Data,
    ReasoningDelta,
    ReasoningStart,
    ResponseGrammar,
    ResponseGrammarError,
    TextDelta,
    TextEnd,
    TextPart,
    TextStart,
    ToolInputAvailable,
    ToolInputDelta,
    ToolInputError,
    ToolInputStart,
    ToolOutputAvailable,
    ToolPart,
    TranscriptMessage,
    apply_chunks,
    chunk_from_wire,
    message_from_wire,
    part_from_wire,
    to_wire,
)

GOLDEN = Path(__file__).parent / "golden"
RECORDS = json.loads((GOLDEN / "transcript_records.json").read_text())
APPLY_CASES = json.loads((GOLDEN / "apply_chunks.json").read_text())


@pytest.mark.parametrize("wire", RECORDS["messages"], ids=lambda m: m["id"])
def test_messages_round_trip(wire: dict) -> None:
    assert to_wire(message_from_wire(wire)) == wire


@pytest.mark.parametrize(
    "wire",
    [part for message in RECORDS["messages"] for part in message["parts"]],
    ids=lambda p: p["type"],
)
def test_parts_round_trip(wire: dict) -> None:
    assert to_wire(part_from_wire(wire)) == wire


@pytest.mark.parametrize("wire", RECORDS["chunks"], ids=lambda c: c["type"])
def test_chunks_round_trip(wire: dict) -> None:
    assert to_wire(chunk_from_wire(wire)) == wire


def test_every_chunk_and_part_type_has_a_golden() -> None:
    chunk_types = {chunk["type"] for chunk in RECORDS["chunks"]}
    part_types = {
        part["type"] for message in RECORDS["messages"] for part in message["parts"]
    }
    tool_states = {
        part["state"]
        for message in RECORDS["messages"]
        for part in message["parts"]
        if part["type"] == "tool"
    }

    assert len(chunk_types) == 19
    assert part_types == {
        "text",
        "reasoning",
        "file",
        "source-url",
        "source-document",
        "data",
        "tool",
    }
    assert len(tool_states) == 7


def test_wire_form_is_camel_case_and_omits_unset_optionals() -> None:
    chunk = ToolInputAvailable(tool_call_id="c1", tool_name="t", input=None)
    assert to_wire(chunk) == {
        "type": "tool-input-available",
        "toolCallId": "c1",
        "toolName": "t",
        "input": None,
    }
    assert to_wire(TextPart(text="hi")) == {"type": "text", "text": "hi"}


def test_a_tool_part_keeps_a_null_output() -> None:
    part = ToolPart(tool_call_id="c", tool_name="t", state="output-available")
    assert to_wire(part)["output"] is None
    pending = ToolPart(tool_call_id="c", tool_name="t", state="input-streaming")
    assert "output" not in to_wire(pending)


@pytest.mark.parametrize(
    ("decode", "wire", "error"),
    [
        (chunk_from_wire, {"type": "text-delta", "id": "t"}, "'delta'"),
        (chunk_from_wire, {"type": "start"}, "Unknown type"),
        (chunk_from_wire, {"id": "t"}, "'type'"),
        (part_from_wire, {"type": "metadata", "metadata": {}}, "Unknown type"),
        (message_from_wire, {"id": "m", "role": "tool", "parts": []}, "role"),
        (message_from_wire, {"id": "m", "role": "user"}, "'parts'"),
        (message_from_wire, {"id": "m", "role": "user", "parts": {}}, "list"),
    ],
)
def test_bad_wire_forms_raise_value_error(decode, wire: dict, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        decode(wire)


@pytest.mark.parametrize("case", APPLY_CASES, ids=lambda case: case["name"])
def test_apply_chunks_matches_upstream(case: dict) -> None:
    message = message_from_wire(case["message"])
    chunks = [chunk_from_wire(chunk) for chunk in case["chunks"]]

    assert to_wire(apply_chunks(message, chunks)) == case["expected"]


def test_apply_chunks_leaves_the_message_unchanged() -> None:
    part = ToolPart(tool_call_id="c", tool_name="t", state="input-available")
    message = TranscriptMessage(id="m", role="assistant", parts=[part])

    applied = apply_chunks(message, [ToolOutputAvailable(tool_call_id="c", output=1)])

    assert part.state == "input-available"
    assert applied.parts[0] == ToolPart(
        tool_call_id="c", tool_name="t", state="output-available", output=1
    )


def test_grammar_accepts_a_well_formed_response() -> None:
    grammar = ResponseGrammar()
    for chunk in (
        TextStart(id="0"),
        ReasoningStart(id="0"),
        TextDelta(id="0", delta="a"),
        ReasoningDelta(id="0", delta="b"),
        TextEnd(id="0"),
        TextStart(id="0"),
        ToolInputStart(tool_call_id="c1", tool_name="t"),
        ToolInputDelta(tool_call_id="c1", delta="{}"),
        ToolInputAvailable(tool_call_id="c1", tool_name="t", input={}),
        ToolInputError(tool_call_id="c2", tool_name="t", input="", error_text="bad"),
        Data(name="d", data=1),
    ):
        grammar.append(chunk)


@pytest.mark.parametrize(
    ("chunks", "error"),
    [
        ([TextDelta(id="t", delta="a")], 'text "t" is not open'),
        ([TextEnd(id="t")], 'text "t" is not open'),
        ([TextStart(id="t"), TextStart(id="t")], 'text "t" is already open'),
        ([TextStart(id="t"), ReasoningDelta(id="t", delta="")], "reasoning"),
        ([ToolInputDelta(tool_call_id="c", delta="")], 'tool input "c" is not open'),
        (
            [
                ToolInputAvailable(tool_call_id="c", tool_name="t", input={}),
                ToolInputDelta(tool_call_id="c", delta=""),
            ],
            "not open",
        ),
    ],
)
def test_grammar_rejects_out_of_order_chunks(chunks: list, error: str) -> None:
    grammar = ResponseGrammar()
    *before, bad = chunks
    for chunk in before:
        grammar.append(chunk)

    with pytest.raises(ResponseGrammarError, match=error):
        grammar.append(bad)


def test_a_rejected_chunk_records_nothing() -> None:
    grammar = ResponseGrammar()
    grammar.append(TextStart(id="t"))
    with pytest.raises(ResponseGrammarError):
        grammar.append(TextStart(id="t"))

    grammar.check(TextDelta(id="t", delta="still open"))
    grammar.append(TextEnd(id="t"))
    grammar.append(TextStart(id="t"))
