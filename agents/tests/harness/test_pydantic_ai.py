import asyncio
import json
from collections.abc import AsyncIterator

from fake_runtime import FakeCtx, Host
from pydantic_ai import (
    Agent,
    BinaryContent,
    CallDeferred,
    ImageUrl,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelRetry,
    NativeToolCallPart,
    NativeToolReturnPart,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    SystemPromptPart,
    ToolCallPart,
    ToolCallPartDelta,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai import FilePart as ModelFilePart
from pydantic_ai import TextPart as ModelTextPart
from pydantic_ai.messages import (
    Citation,
    CompactionPart,
    RetryFeedbackPart,
    RetryInfo,
    WebCitationSource,
)
from pydantic_ai.models.function import (
    AgentInfo,
    DeltaThinkingCalls,
    DeltaThinkingPart,
    DeltaToolCall,
    DeltaToolCalls,
    FunctionModel,
)

from agents.harness import (
    Approval,
    ChunkEvent,
    FileInput,
    HarnessEvent,
    OperationEvent,
    OperationResult,
    TextInput,
    ToolOutput,
    ToolRequest,
    tool,
)
from agents.harness.pydantic_ai import PydanticAIHarness
from agents.harness.pydantic_ai.answers import INTERRUPTED
from agents.harness.pydantic_ai.events import PartIds, to_chunks
from agents.harness.pydantic_ai.utils import MAX_FILE_URL, to_reply
from agents.harness.transcript import (
    DataPart,
    FilePart,
    ReasoningDelta,
    ReasoningEnd,
    ReasoningPart,
    ReasoningStart,
    ResponseChunk,
    ResponseGrammar,
    TextDelta,
    TextEnd,
    TextPart,
    TextStart,
    ToolApproval,
    ToolApprovalRequest,
    ToolInputAvailable,
    ToolInputDelta,
    ToolInputError,
    ToolInputStart,
    ToolOutputAvailable,
    ToolOutputDenied,
    ToolOutputError,
    ToolPart,
    TranscriptMessage,
    apply_chunks,
    to_wire,
)
from agents.lifecycle import Lifecycle

type Call = tuple[str, dict[str, object]]
type Item = str | DeltaToolCalls | DeltaThinkingCalls


def scripted(*calls: Call) -> Agent[object, str]:
    """A model that makes ``calls`` after each prompt, then reports the tool results."""

    async def stream(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | DeltaToolCalls]:
        last = messages[-1]
        assert isinstance(last, ModelRequest)
        returns = [part for part in last.parts if isinstance(part, ToolReturnPart)]
        if returns:
            yield "; ".join(f"{part.tool_name}={part.content}" for part in returns)
        elif calls:
            yield {
                index: DeltaToolCall(name, json.dumps(args), tool_call_id=f"call-{index}")
                for index, (name, args) in enumerate(calls)
            }
        else:
            prompts = [
                part
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, UserPromptPart)
            ]
            yield "prompts="
            yield str(len(prompts))

    return Agent(FunctionModel(stream_function=stream))


def streaming(*responses: list[Item]) -> Agent[object, str]:
    """A model that streams each response in turn."""
    remaining = list(responses)

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Item]:
        for item in remaining.pop(0):
            yield item

    return Agent(FunctionModel(stream_function=stream))


class Notes:
    def __init__(self, *, hang: bool = False) -> None:
        self.hang = hang
        self.started = asyncio.Event()
        self.ran: list[str] = []
        self.retried = False

    @tool(replay="safe")
    async def search(self, query: str) -> str:
        """Search the notes."""
        return await self._run(f"found {query}")

    @tool
    async def add(self, text: str) -> str:
        """Add a note."""
        return await self._run(f"added {text}")

    @tool(requires_approval=True)
    async def delete(self, note_id: int) -> str:
        """Delete a note."""
        return await self._run(f"deleted {note_id}")

    @tool
    async def locate(self) -> str:
        """Ask the user's client where they are."""
        raise CallDeferred

    @tool
    async def flaky(self) -> str:
        """Fails once."""
        if not self.retried:
            self.retried = True
            raise ModelRetry("try again")
        return "ok"

    async def _run(self, result: str) -> str:
        self.ran.append(result)
        self.started.set()
        if self.hang:
            await asyncio.Event().wait()
        return result


def setup(
    agent: Agent[object, str], notes: Notes | None = None, host: Host | None = None
) -> tuple[Lifecycle, PydanticAIHarness, Host]:
    host = host or Host()
    harness = PydanticAIHarness("notes", agent, target=notes or Notes())
    return Lifecycle(host).use(harness), harness, host


async def watched(
    harness: PydanticAIHarness,
    input: object,
    *,
    operation_id: str = "op",
    participant: str | None = None,
) -> tuple[OperationResult, list[HarnessEvent]]:
    """Run one operation; return its result and the events it published."""
    session = harness.session()
    events = session.watch()
    await anext(events)
    await session.submit(input, operation_id=operation_id, participant=participant)
    result = await session.wait(operation_id)

    seen: list[HarnessEvent] = []
    while not (
        isinstance(event := await anext(events), OperationEvent)
        and event.operation_id == operation_id
        and event.status not in ("queued", "running")
    ):
        seen.append(event)
    await events.aclose()
    return result, seen


def chunks_of(events: list[HarnessEvent]) -> list[ResponseChunk]:
    chunks = [event.chunk for event in events if isinstance(event, ChunkEvent)]
    grammar = ResponseGrammar()
    for chunk in chunks:
        grammar.append(chunk)
    return chunks


def transcript(harness: PydanticAIHarness) -> list[TranscriptMessage]:
    return harness._store.messages("")


def live(chunks: list[ResponseChunk], extends: TranscriptMessage | None = None) -> dict:
    """The message a client builds from the chunks, as wire JSON."""
    start = extends or TranscriptMessage(id="op:reply", role="assistant", parts=[])
    return to_wire(apply_chunks(start, chunks))


def test_prompt_runs_the_agent_and_keeps_history() -> None:
    _, harness, _ = setup(scripted())

    async def scenario() -> None:
        first = await harness.prompt("hi")
        second = await harness.prompt("again")

        assert (first.status, first.text) == ("done", "prompts=1")
        assert second.text == "prompts=2"

    asyncio.run(scenario())


def test_text_streams_as_chunks_and_is_saved_as_the_reply() -> None:
    _, harness, _ = setup(scripted())

    async def scenario() -> None:
        _, events = await watched(harness, "hi")

        assert chunks_of(events) == [
            TextStart(id="0:0"),
            TextDelta(id="0:0", delta="prompts="),
            TextDelta(id="0:0", delta="1"),
            TextEnd(id="0:0"),
        ]
        user, reply = transcript(harness)
        assert user == TranscriptMessage(
            id="op", role="user", parts=[TextPart(text="hi")]
        )
        assert reply.id == "op:reply"
        assert reply.parts == [TextPart(text="prompts=1")]
        assert reply.metadata is not None
        assert reply.metadata["modelName"].startswith("function:")
        assert reply.metadata["usage"]["output_tokens"] > 0
        assert live(chunks_of(events))["parts"] == to_wire(reply)["parts"]

    asyncio.run(scenario())


def test_tool_calls_stream_and_the_live_message_matches_the_saved_one() -> None:
    notes = Notes()
    _, harness, _ = setup(scripted(("add", {"text": "milk"})), notes)

    async def scenario() -> None:
        result, events = await watched(harness, "add milk")
        chunks = chunks_of(events)

        assert result.text == "add=added milk"
        assert notes.ran == ["added milk"]
        assert chunks[:4] == [
            ToolInputStart(tool_call_id="call-0", tool_name="add"),
            ToolInputDelta(tool_call_id="call-0", delta='{"text": "milk"}'),
            ToolInputAvailable(
                tool_call_id="call-0", tool_name="add", input={"text": "milk"}
            ),
            ToolOutputAvailable(tool_call_id="call-0", output="added milk"),
        ]
        assert chunks[4] == TextStart(id="1:0")
        reply = transcript(harness)[-1]
        assert reply.parts == [
            ToolPart(
                tool_call_id="call-0",
                tool_name="add",
                state="output-available",
                input={"text": "milk"},
                output="added milk",
            ),
            TextPart(text="add=added milk"),
        ]
        assert live(chunks)["parts"] == to_wire(reply)["parts"]

    asyncio.run(scenario())


def test_thinking_streams_as_reasoning_and_the_signature_is_saved_only() -> None:
    agent = streaming(
        [{0: DeltaThinkingPart(content="Hmm", signature="sig")}, "Done."]
    )
    _, harness, _ = setup(agent)

    async def scenario() -> None:
        _, events = await watched(harness, "think")
        chunks = chunks_of(events)

        assert chunks[:3] == [
            ReasoningStart(id="0:0"),
            ReasoningDelta(id="0:0", delta="Hmm"),
            ReasoningEnd(id="0:0"),
        ]
        reply = transcript(harness)[-1]
        assert reply.parts == [
            ReasoningPart(
                text="Hmm",
                provider_metadata={
                    "pydanticAi": {"signature": "sig", "providerName": "function"}
                },
            ),
            TextPart(text="Done."),
        ]
        # Lossy for live display only: the signature isn't streamed.
        assert live(chunks)["parts"][0] == {"type": "reasoning", "text": "Hmm"}

    asyncio.run(scenario())


def test_approval_stops_the_turn_until_the_client_answers() -> None:
    notes = Notes()
    _, harness, _ = setup(scripted(("delete", {"note_id": 7})), notes)

    async def scenario() -> None:
        stopped, events = await watched(harness, "delete note 7", operation_id="ask")
        assert stopped.status == "needs_input"
        assert stopped.requests == [
            ToolRequest(id="call-0", name="delete", args={"note_id": 7}, kind="approval")
        ]
        assert notes.ran == []
        assert chunks_of(events)[-1] == ToolApprovalRequest(
            tool_call_id="call-0", approval_id="call-0"
        )
        asked = transcript(harness)[-1]
        assert asked.parts == [
            ToolPart(
                tool_call_id="call-0",
                tool_name="delete",
                state="approval-requested",
                input={"note_id": 7},
                approval=ToolApproval(id="call-0"),
            )
        ]
        assert live(chunks_of(events), None)["parts"] == to_wire(asked)["parts"]

        result, events = await watched(harness, [Approval(id="call-0", approved=True)])
        assert result.text == "delete=deleted 7"
        assert chunks_of(events)[0] == ToolOutputAvailable(
            tool_call_id="call-0", output="deleted 7"
        )
        assert [message.id for message in transcript(harness)] == ["ask", "ask:reply"]
        assert transcript(harness)[-1].parts == [
            ToolPart(
                tool_call_id="call-0",
                tool_name="delete",
                state="output-available",
                input={"note_id": 7},
                output="deleted 7",
                approval=ToolApproval(id="call-0", approved=True),
            ),
            TextPart(text="delete=deleted 7"),
        ]

    asyncio.run(scenario())


def test_a_denied_call_does_not_run() -> None:
    notes = Notes()
    _, harness, _ = setup(scripted(("delete", {"note_id": 7})), notes)

    async def scenario() -> None:
        await harness.prompt("delete note 7")
        result, events = await watched(
            harness, [Approval(id="call-0", approved=False, message="keep it")]
        )

        assert result.text == "delete=keep it"
        assert notes.ran == []
        assert chunks_of(events)[0] == ToolOutputDenied(tool_call_id="call-0")
        assert transcript(harness)[-1].parts[0] == ToolPart(
            tool_call_id="call-0",
            tool_name="delete",
            state="output-denied",
            input={"note_id": 7},
            approval=ToolApproval(id="call-0", approved=False, reason="keep it"),
        )

    asyncio.run(scenario())


def test_a_deferred_call_belongs_to_the_participant_who_started_the_turn() -> None:
    _, harness, _ = setup(scripted(("locate", {})))

    async def scenario() -> None:
        stopped, events = await watched(
            harness, "where am I?", operation_id="ask", participant="ann"
        )
        assert stopped.requests == [
            ToolRequest(id="call-0", name="locate", args={}, kind="call")
        ]
        call = transcript(harness)[-1].parts[0]
        assert isinstance(call, ToolPart)
        assert (call.state, call.owner) == ("input-available", "ann")
        # Lossy for live display only: the owner is known once the run ends.
        assert ToolInputStart(tool_call_id="call-0", tool_name="locate") in chunks_of(
            events
        )

        refused, _ = await watched(
            harness, [ToolOutput(id="call-0", value="Paris")], operation_id="b",
            participant="bob",
        )
        assert (refused.status, refused.reason) == ("unanswered", "not-owner")

        result, _ = await watched(
            harness, [ToolOutput(id="call-0", value="London")], operation_id="a",
            participant="ann",
        )
        assert result.text == "locate=London"
        assert transcript(harness)[-1].parts[0] == ToolPart(
            tool_call_id="call-0",
            tool_name="locate",
            state="output-available",
            input={},
            output="London",
            owner="ann",
        )

    asyncio.run(scenario())


def test_a_retried_call_is_an_error_and_the_retry_a_new_part() -> None:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Item]:
        returns = [p for p in messages[-1].parts if isinstance(p, ToolReturnPart)]
        if not returns:
            yield {0: DeltaToolCall("flaky", "{}", tool_call_id="call-0")}
        elif returns[0].outcome == "retried":
            yield {0: DeltaToolCall("flaky", "{}", tool_call_id="call-1")}
        else:
            yield "ok"

    _, harness, _ = setup(Agent(FunctionModel(stream_function=stream)))

    async def scenario() -> None:
        _, events = await watched(harness, "go")
        assert ToolOutputError(tool_call_id="call-0", error_text="try again") in (
            chunks_of(events)
        )
        assert transcript(harness)[-1].parts == [
            ToolPart(
                tool_call_id="call-0",
                tool_name="flaky",
                state="output-error",
                input={},
                error_text="try again",
            ),
            ToolPart(
                tool_call_id="call-1",
                tool_name="flaky",
                state="output-available",
                input={},
                output="ok",
            ),
            TextPart(text="ok"),
        ]

    asyncio.run(scenario())


def test_invalid_tool_input_is_an_input_error() -> None:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Item]:
        if any(isinstance(p, ToolReturnPart) for p in messages[-1].parts):
            yield "sorry"
        else:
            yield {0: DeltaToolCall("add", "{bad", tool_call_id="call-0")}

    _, harness, _ = setup(Agent(FunctionModel(stream_function=stream)))

    async def scenario() -> None:
        _, events = await watched(harness, "go")
        assert ToolInputError(
            tool_call_id="call-0",
            tool_name="add",
            input="{bad",
            error_text="The tool input isn't valid JSON.",
        ) in chunks_of(events)
        call = transcript(harness)[-1].parts[0]
        assert isinstance(call, ToolPart)
        assert (call.state, call.input) == ("output-error", "{bad")

    asyncio.run(scenario())


def test_native_tools_are_provider_executed_tool_parts() -> None:
    agent = streaming(
        [
            {
                0: NativeToolCallPart(
                    tool_name="web_search", args={"q": "x"}, tool_call_id="n1"
                ),
                1: NativeToolReturnPart(
                    tool_name="web_search", content={"hits": 1}, tool_call_id="n1"
                ),
            },
            "Found it.",
        ]
    )
    _, harness, _ = setup(agent)

    async def scenario() -> None:
        _, events = await watched(harness, "search")
        chunks = chunks_of(events)

        assert ToolInputAvailable(
            tool_call_id="n1",
            tool_name="web_search",
            input={"q": "x"},
            provider_executed=True,
        ) in chunks
        assert ToolOutputAvailable(
            tool_call_id="n1", output={"hits": 1}, provider_executed=True
        ) in chunks
        assert transcript(harness)[-1].parts[0] == ToolPart(
            tool_call_id="n1",
            tool_name="web_search",
            state="output-available",
            input={"q": "x"},
            output={"hits": 1},
            provider_executed=True,
        )

    asyncio.run(scenario())


def test_message_parts_reach_the_model_as_user_content() -> None:
    seen: list[object] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        [prompt] = [p for p in messages[-1].parts if isinstance(p, UserPromptPart)]
        seen.extend(prompt.content)
        yield "ok"

    _, harness, _ = setup(Agent(FunctionModel(stream_function=stream)))

    async def scenario() -> None:
        await harness.prompt(
            [
                TextInput(text="What is this?"),
                FileInput(media_type="image/png", url="https://example.com/a.png"),
                FileInput(media_type="image/png", url="data:image/png;base64,AAAA"),
            ]
        )

        assert seen[0] == "What is this?"
        assert seen[1] == ImageUrl(url="https://example.com/a.png", media_type="image/png")
        assert isinstance(seen[2], BinaryContent)
        assert seen[2].data == b"\x00\x00\x00"

    asyncio.run(scenario())


def test_reset_with_a_handoff_seeds_a_system_prompt() -> None:
    seen: list[list[ModelMessage]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        seen.append(list(messages))
        yield "ok"

    _, harness, _ = setup(Agent(FunctionModel(stream_function=stream)))

    async def scenario() -> None:
        await harness.prompt("hi")
        await harness.session().reset("The user likes metric units.")
        await harness.prompt("next")

        first = seen[-1][0]
        assert isinstance(first, ModelRequest)
        assert isinstance(first.parts[0], SystemPromptPart)
        assert first.parts[0].content == "The user likes metric units."
        assert [message.role for message in transcript(harness)] == [
            "system",
            "user",
            "assistant",
        ]
        assert harness.reset_history(None) is None

    asyncio.run(scenario())


def test_resume_reruns_safe_calls_and_reports_unsafe_ones_interrupted() -> None:
    agent = scripted(("search", {"query": "milk"}), ("add", {"text": "milk"}))
    dead_notes = Notes(hang=True)
    _, dead, host = setup(agent, dead_notes)

    async def scenario() -> None:
        await dead.session().submit("add milk", operation_id="op")
        await dead_notes.started.wait()
        # The checkpoint before the tools ran already saved the calls.
        assert [part.tool_call_id for part in transcript(dead)[-1].parts] == [
            "call-0",
            "call-1",
        ]

        notes = Notes()
        restarted = Host()
        restarted.ctx = FakeCtx(storage=host.ctx.storage)
        lifecycle, harness, _ = setup(agent, notes, restarted)
        await lifecycle.start()
        await lifecycle.alarm()
        result = await harness.session().wait("op")

        assert result.text == f"search=found milk; add={INTERRUPTED}"
        assert notes.ran == ["found milk"]
        assert transcript(harness)[-1].parts[1] == ToolPart(
            tool_call_id="call-1",
            tool_name="add",
            state="output-available",
            input={"text": "milk"},
            output=INTERRUPTED,
        )
        await dead.dispose()

    asyncio.run(scenario())


def test_resume_never_approves_a_call_on_the_clients_behalf() -> None:
    agent = scripted(("search", {"query": "x"}), ("delete", {"note_id": 7}))
    dead_notes = Notes(hang=True)
    _, dead, host = setup(agent, dead_notes)

    async def scenario() -> None:
        await dead.session().submit("search and delete", operation_id="op")
        await dead_notes.started.wait()

        notes = Notes()
        restarted = Host()
        restarted.ctx = FakeCtx(storage=host.ctx.storage)
        lifecycle, harness, _ = setup(agent, notes, restarted)
        await lifecycle.start()
        await lifecycle.alarm()
        result = await harness.session().wait("op")

        assert result.text == f"search=found x; delete={INTERRUPTED}"
        assert notes.ran == ["found x"]
        await dead.dispose()

    asyncio.run(scenario())


# Lossy cases, mapped directly


def test_to_reply_drops_citations_feedback_and_internal_parts() -> None:
    messages: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content="hi")]),
        ModelResponse(
            parts=[
                CompactionPart(content="summary"),
                ModelTextPart(
                    content="See this.",
                    citations=[Citation(sources=[WebCitationSource(url="https://x")])],
                ),
            ]
        ),
        ModelRequest(
            parts=[
                RetryFeedbackPart(
                    content="Not valid output", retry=RetryInfo(cause="model_retry")
                )
            ]
        ),
        ModelResponse(parts=[ModelTextPart(content="Better.")]),
    ]

    assert to_reply(messages) == [
        TextPart(text="See this."),
        TextPart(text="Better."),
    ]


def test_to_reply_starts_after_the_last_user_prompt() -> None:
    messages: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content="first")]),
        ModelResponse(parts=[ModelTextPart(content="one")]),
        ModelRequest(parts=[UserPromptPart(content="second")]),
        ModelResponse(parts=[ToolCallPart(tool_name="t", args={}, tool_call_id="c")]),
        ModelRequest(
            parts=[ToolReturnPart(tool_name="t", content=[1, 2], tool_call_id="c")]
        ),
        ModelResponse(parts=[ModelTextPart(content="two")]),
    ]

    assert to_reply(messages) == [
        ToolPart(
            tool_call_id="c",
            tool_name="t",
            state="output-available",
            input={},
            output=[1, 2],
        ),
        TextPart(text="two"),
    ]


def test_model_files_become_data_urls_until_they_are_too_large() -> None:
    small = BinaryContent(data=b"\x89PNG", media_type="image/png")
    large = BinaryContent(data=b"x" * MAX_FILE_URL, media_type="image/png")
    messages: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content="draw")]),
        ModelResponse(parts=[ModelFilePart(content=small), ModelFilePart(content=large)]),
    ]

    assert to_reply(messages) == [
        FilePart(media_type="image/png", url=small.data_uri),
        DataPart(
            name="dropped-file",
            data={"mediaType": "image/png", "bytes": MAX_FILE_URL},
        ),
    ]


def test_dict_tool_args_show_only_once_the_input_is_whole() -> None:
    ids = PartIds()
    ids.next_node()
    call = ToolCallPart(tool_name="t", args={"a": 1}, tool_call_id="c")
    events = [
        PartStartEvent(index=0, part=call),
        PartDeltaEvent(index=0, delta=ToolCallPartDelta(args_delta={"b": 2})),
        PartEndEvent(index=0, part=call),
    ]

    assert [chunk for event in events for chunk in to_chunks(event, ids)] == [
        ToolInputStart(tool_call_id="c", tool_name="t"),
        ToolInputAvailable(tool_call_id="c", tool_name="t", input={"a": 1}),
    ]
