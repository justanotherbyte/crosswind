import json
from pathlib import Path

import pytest

from agents.channels import (
    ApprovalResponse,
    Cancel,
    ClientTool,
    ConversationCreate,
    ConversationFork,
    ConversationInfo,
    ConversationReset,
    InboundMessage,
    Participant,
    QueuedTurn,
    RunningTurn,
    SettledTurn,
    ToolResult,
    ToolResultError,
    ToolResultOk,
    parse_inbound_event,
)
from agents.channels.web import (
    AckFrame,
    CaughtUpFrame,
    ChunksFrame,
    ConversationsFrame,
    EndFrame,
    EventFrame,
    InvalidClientFrame,
    ListConversationsFrame,
    MessagesFrame,
    SnapshotFrame,
    SubscribeFrame,
    TurnFrame,
    frame_to_wire,
    parse_client_frame,
)

GOLDEN = json.loads((Path(__file__).parent / "golden" / "web_frames.json").read_text())


def parse(frame):
    return parse_client_frame(json.dumps(frame))


def invalid_ack(conversation_id, frame):
    # What upstream web/channel.ts answers a malformed frame with.
    return AckFrame(
        conversation_id=conversation_id,
        event_id=frame.event_id or "",
        error=f"Invalid frame: {frame.invalid}",
    )


def test_golden_header_names_the_pinned_commit():
    assert GOLDEN["source"]["commit"].startswith("455b47e")


# Client frames


def test_parses_golden_client_frames():
    hi = [{"type": "text", "text": "hi"}]
    assert parse(GOLDEN["client"]["message"]) == EventFrame(
        event=InboundMessage(
            event_id="e1", message={"id": "e1", "role": "user", "parts": hi}
        )
    )
    # The sender's participant is never read from the frame.
    assert parse(GOLDEN["client"]["message-with-forged-participant"]) == EventFrame(
        event=InboundMessage(
            event_id="e1", message={"id": "m1", "role": "user", "parts": hi}
        )
    )
    assert parse(GOLDEN["client"]["cancel"]) == EventFrame(
        event=Cancel(event_id="c1", turn_id="e1")
    )
    assert parse(GOLDEN["client"]["conversation-reset"]) == EventFrame(
        event=ConversationReset(event_id="r1", handoff="note")
    )
    assert parse(GOLDEN["client"]["conversation-fork"]) == EventFrame(
        event=ConversationFork(event_id="e1")
    )
    assert parse(GOLDEN["client"]["subscribe"]) == SubscribeFrame(response_id="r1")
    assert parse(GOLDEN["client"]["list-conversations"]) == ListConversationsFrame(
        request_id="r1"
    )


@pytest.mark.parametrize("name", list(GOLDEN["client"]))
def test_parsed_client_frames_round_trip_to_their_wire_form(name):
    frame = GOLDEN["client"][name]
    parsed = parse(frame)
    expected = json.loads(json.dumps(frame))
    expected.get("event", {}).pop("participant", None)
    assert frame_to_wire(parsed) == expected


@pytest.mark.parametrize("name", list(GOLDEN["invalid"]))
def test_golden_invalid_frames_get_an_invalid_field_ack(name):
    case = GOLDEN["invalid"][name]
    invalid = parse(case["frame"])
    assert isinstance(invalid, InvalidClientFrame)
    assert frame_to_wire(invalid_ack("default", invalid)) == case["ack"]


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[1, 2]",
        '"channels:event"',
        "{}",
        '{"type": 1}',
        '{"type": "cf_agent_state", "state": {}}',
        '{"type": "channels:snapshot"}',
    ],
)
def test_frames_of_other_protocols_are_not_claimed(text):
    assert parse_client_frame(text) is None


@pytest.mark.parametrize(
    ("frame", "expected"),
    [
        ({"responseId": "r"}, SubscribeFrame(response_id="r")),
        ({"responseId": "", "from": 0}, SubscribeFrame(response_id="", start=0)),
        ({"responseId": "r", "from": 3}, SubscribeFrame(response_id="r", start=3)),
        ({"responseId": "r", "from": 3.0}, SubscribeFrame(response_id="r", start=3)),
        ({}, InvalidClientFrame(invalid="responseId")),
        ({"responseId": 1}, InvalidClientFrame(invalid="responseId")),
        ({"responseId": "r", "from": None}, InvalidClientFrame(invalid="from")),
        ({"responseId": "r", "from": 1.5}, InvalidClientFrame(invalid="from")),
        ({"responseId": "r", "from": "1"}, InvalidClientFrame(invalid="from")),
        ({"responseId": "r", "from": True}, InvalidClientFrame(invalid="from")),
    ],
)
def test_subscribe(frame, expected):
    assert parse({"type": "channels:subscribe", **frame}) == expected


@pytest.mark.parametrize("request_id", [None, "", 7])
def test_list_conversations_needs_a_request_id(request_id):
    frame = {"type": "channels:list-conversations", "requestId": request_id}
    assert parse(frame) == InvalidClientFrame(invalid="requestId")


@pytest.mark.parametrize("event", [None, "e", [], {}, {"eventId": ""}, {"eventId": 1}])
def test_an_event_without_an_id_is_acked_with_an_empty_id(event):
    invalid = parse({"type": "channels:event", "event": event})
    assert invalid == InvalidClientFrame(invalid="eventId")
    assert frame_to_wire(invalid_ack("c", invalid)) == {
        "type": "channels:ack",
        "conversationId": "c",
        "eventId": "",
        "error": "Invalid frame: eventId",
    }


def test_a_missing_event_is_invalid():
    assert parse({"type": "channels:event"}) == InvalidClientFrame(invalid="eventId")


# Inbound events: the first invalid field, as upstream parseEvent names it

USER = {"id": "m", "role": "user", "parts": []}


@pytest.mark.parametrize(
    ("event", "field"),
    [
        ({"type": "nope"}, "type"),
        ({}, "type"),
        ({"type": "message"}, "message.id"),
        ({"type": "message", "message": []}, "message.id"),
        ({"type": "message", "message": {"id": ""}}, "message.id"),
        ({"type": "message", "message": {"id": "m"}}, "message.role"),
        ({"type": "message", "message": {**USER, "role": "assistant"}}, "message.role"),
        ({"type": "message", "message": {**USER, "parts": None}}, "message.parts"),
        ({"type": "message", "message": {**USER, "parts": ["hi"]}}, "message.parts"),
        ({"type": "message", "message": {**USER, "parts": [{}]}}, "message.parts"),
        (
            {"type": "message", "message": {**USER, "parts": [{"type": 1}]}},
            "message.parts",
        ),
        ({"type": "tool-result"}, "turnId"),
        ({"type": "tool-result", "turnId": "t"}, "toolCallId"),
        ({"type": "tool-result", "turnId": "t", "toolCallId": "c"}, "result"),
        (
            {"type": "tool-result", "turnId": "t", "toolCallId": "c", "result": {}},
            "result",
        ),
        (
            {
                "type": "tool-result",
                "turnId": "t",
                "toolCallId": "c",
                "result": {"ok": 1},
            },
            "result",
        ),
        ({"type": "approval-response", "approvalId": "a"}, "turnId"),
        ({"type": "approval-response", "turnId": "t"}, "approvalId"),
        (
            {"type": "approval-response", "turnId": "t", "approvalId": "a"},
            "approved",
        ),
        (
            {
                "type": "approval-response",
                "turnId": "t",
                "approvalId": "a",
                "approved": "yes",
            },
            "approved",
        ),
        ({"type": "cancel"}, "turnId"),
        ({"type": "cancel", "turnId": ""}, "turnId"),
        ({"type": "conversation-reset", "handoff": None}, "handoff"),
        ({"type": "conversation-reset", "handoff": 1}, "handoff"),
    ],
)
def test_invalid_event_fields(event, field):
    event = {"eventId": "e", **event}
    assert parse_inbound_event(event) == field
    frame = parse({"type": "channels:event", "event": event})
    assert frame == InvalidClientFrame(invalid=field, event_id="e")
    assert frame_to_wire(invalid_ack("c", frame))["eventId"] == "e"


def test_message_keeps_object_metadata_and_named_client_tools():
    event = parse_inbound_event(
        {
            "type": "message",
            "eventId": "e",
            "message": {
                **USER,
                "parts": [{"type": "anything", "extra": 1}],
                "metadata": {"k": 1},
                "extra": True,
            },
            "clientTools": [
                {"name": "a", "description": "A", "inputSchema": {"type": "object"}},
                {"name": "b", "description": 1, "inputSchema": []},
                {"name": ""},
                {"description": "no name"},
                "c",
            ],
        }
    )
    assert event == InboundMessage(
        event_id="e",
        message={
            "id": "m",
            "role": "user",
            "parts": [{"type": "anything", "extra": 1}],
            "metadata": {"k": 1},
        },
        client_tools=[
            ClientTool(name="a", description="A", input_schema={"type": "object"}),
            ClientTool(name="b"),
        ],
    )


def test_message_drops_metadata_and_client_tools_of_the_wrong_type():
    event = parse_inbound_event(
        {
            "type": "message",
            "eventId": "e",
            "message": {**USER, "metadata": []},
            "clientTools": {"name": "a"},
        }
    )
    assert event == InboundMessage(event_id="e", message=USER)
    # An empty list is kept: the client advertised no tools.
    event = parse_inbound_event(
        {"type": "message", "eventId": "e", "message": USER, "clientTools": []}
    )
    assert event == InboundMessage(event_id="e", message=USER, client_tools=[])


def test_tool_results():
    base = {"type": "tool-result", "eventId": "e", "turnId": "t", "toolCallId": "c"}
    ok = parse_inbound_event({**base, "result": {"ok": True, "output": {"n": 1}}})
    assert ok == ToolResult(
        event_id="e", turn_id="t", tool_call_id="c", result=ToolResultOk(output={"n": 1})
    )
    # A missing output is null, and null is written back.
    ok = parse_inbound_event({**base, "result": {"ok": True}})
    assert ok.result == ToolResultOk(output=None)
    assert frame_to_wire(EventFrame(event=ok))["event"]["result"] == {
        "ok": True,
        "output": None,
    }
    error = parse_inbound_event(
        {**base, "result": {"ok": False, "errorText": "boom"}, "clientTools": []}
    )
    assert error == ToolResult(
        event_id="e",
        turn_id="t",
        tool_call_id="c",
        result=ToolResultError(error_text="boom"),
        client_tools=[],
    )
    error = parse_inbound_event({**base, "result": {"ok": False, "errorText": 1}})
    assert frame_to_wire(EventFrame(event=error))["event"]["result"] == {"ok": False}


def test_approval_responses_and_conversation_operations():
    approval = {"type": "approval-response", "eventId": "e", "turnId": "t"}
    assert parse_inbound_event(
        {**approval, "approvalId": "a", "approved": False, "reason": "no"}
    ) == ApprovalResponse(
        event_id="e", turn_id="t", approval_id="a", approved=False, reason="no"
    )
    assert parse_inbound_event(
        {**approval, "approvalId": "a", "approved": True, "reason": 1}
    ) == ApprovalResponse(event_id="e", turn_id="t", approval_id="a", approved=True)
    create = {"type": "conversation-create", "eventId": "e"}
    assert parse_inbound_event(create) == ConversationCreate(event_id="e")
    # An empty handoff is no handoff, as upstream drops it.
    reset = {"type": "conversation-reset", "eventId": "e", "handoff": ""}
    assert parse_inbound_event(reset) == ConversationReset(event_id="e")
    assert frame_to_wire(EventFrame(event=ConversationReset(event_id="e"))) == {
        "type": "channels:event",
        "event": {"type": "conversation-reset", "eventId": "e"},
    }


# Server frames: the records write upstream's golden frames


def server_records():
    hi = [{"type": "text", "text": "hi"}]
    return {
        "snapshot": SnapshotFrame(
            conversation_id="default",
            you=Participant(id="alice"),
            operations=["conversation-create", "conversation-fork", "conversation-reset"],
            messages=[{"id": "m1", "role": "user", "parts": []}],
            turns=[QueuedTurn(turn_id="t1", started_by="t1")],
        ),
        "snapshot-client": SnapshotFrame(
            conversation_id="main",
            you=Participant(id="alice"),
            operations=["conversation-create"],
        ),
        "ack": AckFrame(conversation_id="default", event_id="e1"),
        "ack-rejected": AckFrame(
            conversation_id="default", event_id="e2", error="Event rejected"
        ),
        "ack-fork": AckFrame(conversation_id="s1", event_id="e1"),
        "messages": MessagesFrame(
            conversation_id="default",
            messages=[{"id": "e1", "role": "user", "parts": hi}],
        ),
        "chunks": ChunksFrame(
            conversation_id="default",
            response_id="r1",
            start=0,
            chunks=[
                {"type": "text-start", "id": "a"},
                {"type": "text-delta", "id": "a", "delta": "He"},
            ],
        ),
        "chunks-live": ChunksFrame(
            conversation_id="default",
            response_id="r1",
            start=2,
            chunks=[{"type": "text-delta", "id": "a", "delta": "y"}],
        ),
        "caught-up": CaughtUpFrame(conversation_id="default", response_id="r1", cursor=2),
        "end-ended": EndFrame(conversation_id="default", response_id="r1", state="ended"),
        "end-interrupted": EndFrame(
            conversation_id="default", response_id="r1", state="interrupted"
        ),
        "end-not-found": EndFrame(
            conversation_id="default", response_id="r1", state="not-found"
        ),
        "turn-failed": TurnFrame(
            conversation_id="default",
            turn=SettledTurn(
                turn_id="e1",
                started_by="e1",
                outcome="failed",
                message_ids=[],
                error="Not answered: boom",
            ),
        ),
        "conversations-answer": ConversationsFrame(
            conversation_id="default",
            request_id="r1",
            conversations=[ConversationInfo(id="default", busy=False)],
        ),
        "conversations-pushed": ConversationsFrame(
            conversation_id="s1",
            conversations=[
                ConversationInfo(id="default", busy=False),
                ConversationInfo(id="s1", parent="default", busy=False),
            ],
        ),
        "conversations-client": ConversationsFrame(
            conversation_id="main",
            request_id="q1",
            conversations=[
                ConversationInfo(id="main", busy=False),
                ConversationInfo(id="f1", parent="main", busy=True),
            ],
        ),
    }


def test_every_golden_server_frame_has_a_record():
    assert set(server_records()) == set(GOLDEN["server"])


@pytest.mark.parametrize("name", list(GOLDEN["server"]))
def test_server_frames_match_upstream(name):
    wire = frame_to_wire(server_records()[name])
    assert wire == GOLDEN["server"][name]
    assert json.loads(json.dumps(wire)) == wire


@pytest.mark.parametrize(
    ("turn", "expected"),
    [
        (
            QueuedTurn(turn_id="t", started_by="e"),
            {"turnId": "t", "startedBy": "e", "status": "queued"},
        ),
        (
            RunningTurn(turn_id="t", started_by="e", response_id="r"),
            {"turnId": "t", "startedBy": "e", "status": "running", "responseId": "r"},
        ),
        (
            RunningTurn(turn_id="t", started_by="e", response_id="r", extends="m"),
            {
                "turnId": "t",
                "startedBy": "e",
                "status": "running",
                "responseId": "r",
                "extends": "m",
            },
        ),
        (
            SettledTurn(
                turn_id="t", started_by="e", outcome="awaiting-input", message_ids=["m"]
            ),
            {
                "turnId": "t",
                "startedBy": "e",
                "status": "settled",
                "outcome": "awaiting-input",
                "messageIds": ["m"],
            },
        ),
    ],
)
def test_turn_statuses(turn, expected):
    assert frame_to_wire(TurnFrame(conversation_id="c", turn=turn))["turn"] == expected


def test_a_failed_list_answer_carries_only_its_error():
    frame = ConversationsFrame(conversation_id="c", request_id="q", error="nope")
    assert frame_to_wire(frame) == {
        "type": "channels:conversations",
        "conversationId": "c",
        "requestId": "q",
        "error": "nope",
    }


def test_participant_name_is_written_when_set():
    frame = SnapshotFrame(conversation_id="c", you=Participant(id="a", name="Ada"))
    assert frame_to_wire(frame)["you"] == {"id": "a", "name": "Ada"}
