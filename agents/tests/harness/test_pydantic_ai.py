import asyncio
import json
from collections.abc import AsyncIterator

from fake_runtime import FakeCtx, Host
from pydantic_ai import Agent, ModelMessage, ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel

from agents.harness import Approval, OperationEvent, TextEvent, ToolCallEvent, ToolRequest, tool
from agents.harness.pydantic_ai import PydanticAIHarness
from agents.harness.pydantic_ai.answers import INTERRUPTED
from agents.lifecycle import Lifecycle

type Call = tuple[str, dict[str, object]]


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


class Notes:
    def __init__(self, *, hang: bool = False) -> None:
        self.hang = hang
        self.started = asyncio.Event()
        self.ran: list[str] = []

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

    async def _run(self, result: str) -> str:
        self.ran.append(result)
        self.started.set()
        if self.hang:
            await asyncio.Event().wait()
        return result


def setup(
    agent: Agent[object, str], notes: Notes, host: Host | None = None
) -> tuple[Lifecycle, PydanticAIHarness, Host]:
    host = host or Host()
    harness = PydanticAIHarness("notes", agent, target=notes)
    return Lifecycle(host).use(harness), harness, host


def test_prompt_runs_the_agent_and_keeps_history() -> None:
    _, harness, _ = setup(scripted(), Notes())

    async def scenario() -> None:
        first = await harness.prompt("hi")
        second = await harness.prompt("again")

        assert (first.status, first.text) == ("done", "prompts=1")
        assert second.text == "prompts=2"

    asyncio.run(scenario())


def test_the_targets_tools_run_and_stream_events() -> None:
    notes = Notes()
    _, harness, _ = setup(scripted(("add", {"text": "milk"})), notes)

    async def scenario() -> None:
        events = harness.session().watch()
        await anext(events)

        result = await harness.session().prompt("add milk", operation_id="op")
        seen = [await anext(events) for _ in range(6)]
        await events.aclose()

        assert result.text == "add=added milk"
        assert notes.ran == ["added milk"]
        assert seen[2] == ToolCallEvent(
            operation_id="op", tool_call_id="call-0", name="add", args={"text": "milk"}
        )
        assert seen[4] == TextEvent(operation_id="op", delta="add=added milk")
        assert seen[5] == OperationEvent(operation_id="op", status="done")

    asyncio.run(scenario())


def test_approval_stops_the_turn_until_the_client_answers() -> None:
    notes = Notes()
    _, harness, _ = setup(scripted(("delete", {"note_id": 7})), notes)

    async def scenario() -> None:
        stopped = await harness.prompt("delete note 7")
        assert stopped.status == "needs_input"
        assert stopped.requests == [
            ToolRequest(id="call-0", name="delete", args={"note_id": 7}, kind="approval")
        ]
        assert notes.ran == []

        result = await harness.prompt([Approval(id="call-0", approved=True)])
        assert result.text == "delete=deleted 7"

    asyncio.run(scenario())


def test_a_denied_call_does_not_run() -> None:
    notes = Notes()
    _, harness, _ = setup(scripted(("delete", {"note_id": 7})), notes)

    async def scenario() -> None:
        await harness.prompt("delete note 7")
        result = await harness.prompt(
            [Approval(id="call-0", approved=False, message="keep it")]
        )

        assert result.text == "delete=keep it"
        assert notes.ran == []

    asyncio.run(scenario())


def test_resume_reruns_safe_calls_and_reports_unsafe_ones_interrupted() -> None:
    agent = scripted(("search", {"query": "milk"}), ("add", {"text": "milk"}))
    dead_notes = Notes(hang=True)
    _, dead, host = setup(agent, dead_notes)

    async def scenario() -> None:
        await dead.session().submit("add milk", operation_id="op")
        await dead_notes.started.wait()

        notes = Notes()
        restarted = Host()
        restarted.ctx = FakeCtx(storage=host.ctx.storage)
        lifecycle, harness, _ = setup(agent, notes, restarted)
        await lifecycle.start()
        await lifecycle.alarm()
        result = await harness.session().wait("op")

        assert result.text == f"search=found milk; add={INTERRUPTED}"
        assert notes.ran == ["found milk"]
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
