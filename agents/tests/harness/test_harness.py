import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest
from fake_runtime import FakeCtx, Host

from agents.harness import (
    Approval,
    ChunkEvent,
    FileInput,
    Harness,
    MessageEvent,
    OperationEvent,
    OperationNotFoundError,
    ResetEvent,
    RunEndEvent,
    RunSnapshot,
    RunStartEvent,
    SessionBusyError,
    SessionInfo,
    SessionNotFoundError,
    SessionSnapshot,
    TextInput,
    ToolOutput,
    ToolRequest,
    Turn,
    TurnResult,
    tool,
)
from agents.harness.store import HarnessStore
from agents.harness.transcript import (
    FilePart,
    TextDelta,
    TextEnd,
    TextPart,
    TextStart,
    ToolApproval,
    ToolPart,
    TranscriptMessage,
)
from agents.lifecycle import Lifecycle, MemoryLimitContext

type Script = Callable[[Turn], Awaitable[TurnResult]]


def chunks(turn: Turn, text: str) -> list[ChunkEvent]:
    """The chunk events `echo` streams for `text`."""
    return [
        ChunkEvent(operation_id=turn.operation_id, chunk=chunk)
        for chunk in (TextStart(id="0"), TextDelta(id="0", delta=text), TextEnd(id="0"))
    ]


async def echo(turn: Turn) -> TurnResult:
    """Append the prompt to the history, stream it, and answer with it."""
    for event in chunks(turn, str(turn.input)):
        turn.emit(event)

    history = (turn.history or b"") + str(turn.input).encode() + b";"
    return TurnResult(
        history=history,
        reply=[TextPart(text=f"echo {turn.input}")],
        text=f"echo {turn.input}",
    )


class ScriptedHarness(Harness):
    def __init__(
        self,
        script: Script = echo,
        *,
        id: str = "test-harness",
        target: object = None,
        max_restarts: int = 5,
    ) -> None:
        super().__init__(id, target=target, max_restarts=max_restarts)

        self.script = script
        self.turns: list[Turn] = []

    async def run_turn(self, turn: Turn) -> TurnResult:
        self.turns.append(turn)
        return await self.script(turn)


def setup(
    script: Script = echo, host: Host | None = None, *, max_restarts: int = 5
) -> tuple[Lifecycle, ScriptedHarness, Host]:
    host = host or Host()
    harness = ScriptedHarness(script, max_restarts=max_restarts)
    return Lifecycle(host).use(harness), harness, host


def restarted(host: Host) -> Host:
    """A new host on the same storage: the object after its isolate died."""
    new = Host()
    new.ctx = FakeCtx(storage=host.ctx.storage)
    return new


class Blocker:
    """A script that saves a checkpoint, then blocks until released."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, turn: Turn) -> TurnResult:
        turn.save(
            b"checkpoint:" + str(turn.input).encode(),
            reply=[TextPart(text="partial")],
        )
        turn.emit(ChunkEvent(operation_id=turn.operation_id, chunk=TextStart(id="0")))
        self.started.set()
        await self.release.wait()
        return await echo(turn)


def test_prompt_runs_a_turn_and_keeps_history() -> None:
    _, harness, _ = setup()

    async def scenario() -> None:
        first = await harness.prompt("hi")
        second = await harness.prompt("again")

        assert (first.status, first.text) == ("done", "echo hi")
        assert second.text == "echo again"
        assert harness.turns[1].history == b"hi;"
        assert harness.turns[0].attempt == 0

    asyncio.run(scenario())


def test_submit_dedupes_by_operation_id() -> None:
    _, harness, _ = setup()

    async def scenario() -> None:
        session = harness.session()
        first = await session.submit("hi", operation_id="op-1")
        second = await session.submit("hi", operation_id="op-1")
        await session.wait("op-1")

        assert (first.accepted, second.accepted) == (True, False)
        assert len(harness.turns) == 1

    asyncio.run(scenario())


def test_operations_run_one_at_a_time_in_order() -> None:
    blocker = Blocker()
    _, harness, _ = setup(blocker)

    async def scenario() -> None:
        session = harness.session()
        await session.submit("a", operation_id="a")
        await session.submit("b", operation_id="b")
        await blocker.started.wait()
        assert [op.status for op in await session.pending()] == ["running", "queued"]

        blocker.release.set()
        await session.wait("b")
        assert [turn.input for turn in harness.turns] == ["a", "b"]

    asyncio.run(scenario())


def test_reject_when_busy() -> None:
    blocker = Blocker()
    _, harness, _ = setup(blocker)

    async def scenario() -> None:
        session = harness.session()
        await session.submit("a", operation_id="a")
        with pytest.raises(SessionBusyError):
            await session.submit("b", when_busy="reject")

        receipt = await session.submit("a", operation_id="a", when_busy="reject")
        assert not receipt.accepted

        blocker.release.set()
        await session.wait("a")

    asyncio.run(scenario())


def test_wait_for_an_unknown_operation_raises() -> None:
    _, harness, _ = setup()

    async def scenario() -> None:
        with pytest.raises(OperationNotFoundError):
            await harness.session().wait("nope")

    asyncio.run(scenario())


def test_failing_turn_settles_unanswered_and_retry_resumes() -> None:
    failures = [RuntimeError("model down")]

    async def flaky(turn: Turn) -> TurnResult:
        turn.save(b"step-1")
        if failures:
            raise failures.pop()

        return await echo(turn)

    _, harness, _ = setup(flaky)

    async def scenario() -> None:
        session = harness.session()
        failed = await session.prompt("hi", operation_id="op")
        assert (failed.status, failed.reason) == ("unanswered", "model down")

        await session.retry("op")
        result = await session.wait("op")
        assert result.status == "done"
        assert harness.turns[1].checkpoint == b"step-1"

        with pytest.raises(ValueError, match="unanswered"):
            await session.retry("op")

    asyncio.run(scenario())


def test_retry_is_refused_after_a_later_operation_ran() -> None:
    failures = [RuntimeError("model down")]

    async def flaky(turn: Turn) -> TurnResult:
        turn.save(b"step-1")
        if failures:
            raise failures.pop()

        return await echo(turn)

    _, harness, _ = setup(flaky)

    async def scenario() -> None:
        session = harness.session()
        await session.prompt("hi", operation_id="a")
        assert (await session.prompt("next", operation_id="b")).status == "done"

        with pytest.raises(ValueError, match="later operations"):
            await session.retry("a")

    asyncio.run(scenario())


def test_needs_input_then_answers_as_a_new_operation() -> None:
    request = ToolRequest(id="call-1", name="delete", args={"id": 3}, kind="approval")

    async def approval(turn: Turn) -> TurnResult:
        if isinstance(turn.input, str):
            return TurnResult(
                history=b"asked", status="needs_input", requests=[request]
            )

        return TurnResult(history=b"done", text=f"answered {turn.input[0].id}")

    _, harness, _ = setup(approval)

    async def scenario() -> None:
        session = harness.session()
        asked = await session.prompt("delete note 3")
        assert (asked.status, asked.requests) == ("needs_input", [request])

        answered = await session.prompt([Approval(id="call-1", approved=True)])
        assert answered.text == "answered call-1"
        assert harness.turns[1].input == [Approval(id="call-1", approved=True)]

    asyncio.run(scenario())


def test_abort_cancels_the_running_turn_and_runs_the_next() -> None:
    blocker = Blocker()
    _, harness, _ = setup(blocker)

    async def scenario() -> None:
        session = harness.session()
        await session.submit("a", operation_id="a")
        await session.submit("b", operation_id="b")
        await blocker.started.wait()
        await session.abort("a")
        assert (await session.wait("a")).status == "aborted"

        blocker.release.set()
        assert (await session.wait("b")).status == "done"
        assert harness.lifecycle.storage.sql.exec(
            "SELECT history FROM cf_agents_harness_sessions WHERE id = ''"
        ).toArray() == [{"history": b"b;"}]

    asyncio.run(scenario())


def test_wake_job_resumes_a_turn_from_its_checkpoint_after_a_crash() -> None:
    blocker = Blocker()
    _, dead, host = setup(blocker)

    async def scenario() -> None:
        await dead.session().submit("hi", operation_id="op")
        await blocker.started.wait()

        lifecycle, harness, _ = setup(host=restarted(host))
        await lifecycle.start()
        await lifecycle.alarm()
        result = await harness.session().wait("op")

        assert result.text == "echo hi"
        assert harness.turns[0].checkpoint == b"checkpoint:hi"
        assert harness.turns[0].attempt == 1
        await dead.dispose()

    asyncio.run(scenario())


def test_turn_settles_unanswered_after_max_restarts() -> None:
    blocker = Blocker()
    _, dead, host = setup(blocker)

    async def scenario() -> None:
        await dead.session().submit("hi", operation_id="op")
        await blocker.started.wait()
        host.ctx.storage.sql.exec(
            "UPDATE cf_agents_harness_operations SET attempts = 2"
        )

        lifecycle, harness, _ = setup(host=restarted(host), max_restarts=2)
        await lifecycle.start()
        await lifecycle.alarm()
        result = await harness.session().wait("op")

        assert (result.status, result.reason) == ("unanswered", "interrupted")
        assert harness.turns == []
        await dead.dispose()

    asyncio.run(scenario())


def test_platform_failure_leaves_the_turn_running_for_recovery() -> None:
    async def evicted(turn: Turn) -> TurnResult:
        turn.save(b"step-1")
        raise RuntimeError("Durable Object reset because its code was updated.")

    _, harness, _ = setup(evicted)

    async def scenario() -> None:
        await harness.session().submit("hi", operation_id="op")
        await asyncio.sleep(0)

        [pending] = await harness.session().pending()
        assert pending.status == "running"

    asyncio.run(scenario())


def test_two_harnesses_share_one_object() -> None:
    host = Host()
    first = ScriptedHarness(id="first")
    second = ScriptedHarness(id="second")
    Lifecycle(host).use(first).use(second)

    async def scenario() -> None:
        assert (await first.prompt("a")).text == "echo a"
        assert (await second.prompt("b")).text == "echo b"

    asyncio.run(scenario())


def test_sealed_memory_limit_settles_running_turns() -> None:
    blocker = Blocker()
    _, harness, _ = setup(blocker)

    async def scenario() -> None:
        await harness.session().submit("hi", operation_id="op")
        await blocker.started.wait()
        await harness.on_memory_limit(MemoryLimitContext(sealed=True))
        result = await harness.session().wait("op")

        assert (result.status, result.reason) == ("unanswered", "out_of_memory")
        await harness.dispose()

    asyncio.run(scenario())


def test_wake_job_completes_once_no_work_is_open() -> None:
    lifecycle, harness, _ = setup()

    async def scenario() -> None:
        await harness.prompt("hi")
        await lifecycle.alarm()

        assert harness.lifecycle.jobs.list() == []

    asyncio.run(scenario())


def test_fork_copies_history_and_reset_clears_it() -> None:
    _, harness, _ = setup()

    async def scenario() -> None:
        await harness.prompt("hi")
        fork = await harness.sessions.fork("")
        await fork.prompt("more")
        assert harness.turns[-1].history == b"hi;"

        await harness.session().reset()
        await harness.prompt("fresh")
        assert harness.turns[-1].history is None

        assert await harness.sessions.list() == [
            SessionInfo(id=""),
            SessionInfo(id=fork.id, parent=""),
        ]
        with pytest.raises(SessionNotFoundError):
            await harness.sessions.fork("missing")

    asyncio.run(scenario())


def test_watch_yields_a_snapshot_then_events() -> None:
    _, harness, _ = setup()

    async def scenario() -> None:
        events = harness.session().watch()
        assert await anext(events) == SessionSnapshot(messages=[], pending=[])

        await harness.session().submit("hi", operation_id="op", participant="ann")
        await harness.session().wait("op")
        seen = [await anext(events) for _ in range(10)]
        user = TranscriptMessage(id="op", role="user", parts=[TextPart(text="hi")])
        reply = TranscriptMessage(
            id="op:reply", role="assistant", parts=[TextPart(text="echo hi")]
        )
        assert seen == [
            OperationEvent(operation_id="op", status="queued"),
            MessageEvent(message=user),
            OperationEvent(operation_id="op", status="running"),
            RunStartEvent(operations=("op",)),
            *chunks(harness.turns[0], "hi"),
            MessageEvent(message=reply),
            RunEndEvent(operations=("op",)),
            OperationEvent(operation_id="op", status="done"),
        ]
        assert harness.turns[0].participant == "ann"
        await events.aclose()

    asyncio.run(scenario())


def test_stream_yields_only_its_operation_and_ends_when_it_settles() -> None:
    _, harness, _ = setup()

    async def scenario() -> None:
        session = harness.session()
        await session.submit("first", operation_id="other")

        seen = [event async for event in session.stream("hi", operation_id="op")]
        assert seen == [
            OperationEvent(operation_id="op", status="queued"),
            OperationEvent(operation_id="op", status="running"),
            RunStartEvent(operations=("op",)),
            *chunks(harness.turns[1], "hi"),
            RunEndEvent(operations=("op",)),
            OperationEvent(operation_id="op", status="done"),
        ]

    asyncio.run(scenario())


def test_stream_of_a_settled_operation_yields_nothing() -> None:
    _, harness, _ = setup()

    async def scenario() -> None:
        session = harness.session()
        await session.prompt("hi", operation_id="op")

        assert [event async for event in session.stream("hi", operation_id="op")] == []
        assert len(harness.turns) == 1

    asyncio.run(scenario())


def test_stream_of_an_open_duplicate_follows_the_operation() -> None:
    blocker = Blocker()
    _, harness, _ = setup(blocker)

    async def scenario() -> None:
        session = harness.session()
        await session.submit("hi", operation_id="op")
        await blocker.started.wait()

        follower = asyncio.ensure_future(
            asyncio.wait_for(
                _collect(session.stream("hi", operation_id="op")), timeout=1
            )
        )
        await asyncio.sleep(0)
        blocker.release.set()

        assert (await follower)[-1] == OperationEvent(operation_id="op", status="done")

    asyncio.run(scenario())


def test_stream_ends_when_the_operation_is_aborted() -> None:
    blocker = Blocker()
    _, harness, _ = setup(blocker)

    async def scenario() -> None:
        session = harness.session()
        follower = asyncio.ensure_future(
            asyncio.wait_for(
                _collect(session.stream("hi", operation_id="op")), timeout=1
            )
        )
        await blocker.started.wait()
        await session.abort("op")

        assert (await follower)[-1] == OperationEvent(
            operation_id="op", status="aborted"
        )

    asyncio.run(scenario())


async def _collect[T](events: AsyncIterator[T]) -> list[T]:
    return [event async for event in events]


class Notes:
    @tool(replay="safe")
    async def search(self, query: str) -> list[str]:
        return [query]

    @tool(requires_approval=True)
    async def delete(self, note_id: int) -> str:
        return f"deleted {note_id}"

    async def helper(self) -> None: ...


class Overridden(Notes):
    async def delete(self, note_id: int) -> str:
        return ""


def test_tools_are_the_targets_marked_methods_bound() -> None:
    harness = ScriptedHarness(target=Notes())
    specs = {spec.name: spec for spec in harness.tools}

    assert set(specs) == {"search", "delete"}
    assert (specs["search"].replay, specs["search"].requires_approval) == (
        "safe",
        False,
    )
    assert specs["delete"].requires_approval
    assert asyncio.run(specs["search"].fn("x")) == ["x"]

    assert [spec.name for spec in ScriptedHarness(target=Overridden()).tools] == [
        "search"
    ]


def test_tool_rejects_sync_functions_and_bad_replay() -> None:
    with pytest.raises(TypeError, match="async"):

        @tool
        def sync(self) -> None: ...

    with pytest.raises(ValueError, match="replay"):
        tool(replay="sometimes")


# Transcript


def transcript(harness: Harness, session: str = "") -> list[TranscriptMessage]:
    return harness._store.messages(session)


def test_a_turn_writes_one_user_and_one_assistant_message() -> None:
    _, harness, _ = setup()

    async def scenario() -> None:
        session = harness.session()
        await session.submit(
            [TextInput(text="look"), FileInput(media_type="image/png", url="u")],
            operation_id="op-1",
            message_id="m-1",
        )
        await session.wait("op-1")
        await session.prompt("again", operation_id="op-2")

        assert transcript(harness) == [
            TranscriptMessage(
                id="m-1",
                role="user",
                parts=[TextPart(text="look"), FilePart(media_type="image/png", url="u")],
            ),
            TranscriptMessage(
                id="op-1:reply",
                role="assistant",
                parts=[TextPart(text=f"echo {harness.turns[0].input}")],
            ),
            TranscriptMessage(id="op-2", role="user", parts=[TextPart(text="again")]),
            TranscriptMessage(
                id="op-2:reply", role="assistant", parts=[TextPart(text="echo again")]
            ),
        ]

    asyncio.run(scenario())


def test_queued_input_waits_for_the_running_turn_to_finish_its_messages() -> None:
    blocker = Blocker()
    _, harness, _ = setup(blocker)

    async def scenario() -> None:
        session = harness.session()
        await session.submit("a", operation_id="a")
        await session.submit("b", operation_id="b")
        await blocker.started.wait()
        assert [message.id for message in transcript(harness)] == ["a", "a:reply"]

        blocker.release.set()
        await session.wait("b")
        assert [message.id for message in transcript(harness)] == [
            "a",
            "a:reply",
            "b",
            "b:reply",
        ]

    asyncio.run(scenario())


def test_input_must_be_a_prompt_parts_or_answers() -> None:
    _, harness, _ = setup()

    async def scenario() -> None:
        with pytest.raises(TypeError, match="mixes"):
            await harness.session().submit(
                [TextInput(text="hi"), Approval(id="x", approved=True)]
            )
        with pytest.raises(ValueError, match="at least one"):
            await harness.session().submit([])

    asyncio.run(scenario())


def asks(*requests: ToolRequest) -> Script:
    """A script that stops on ``requests`` after a prompt and echoes answers."""

    async def script(turn: Turn) -> TurnResult:
        if isinstance(turn.input, str):
            return TurnResult(
                history=b"asked",
                reply=[
                    TextPart(text="Let me check."),
                    *(
                        ToolPart(
                            tool_call_id=request.id,
                            tool_name=request.name,
                            state="approval-requested"
                            if request.kind == "approval"
                            else "input-available",
                            input=request.args,
                            approval=ToolApproval(id=request.id)
                            if request.kind == "approval"
                            else None,
                            owner=turn.participant if request.kind == "call" else None,
                        )
                        for request in requests
                    ),
                ],
                status="needs_input",
                requests=requests,
            )

        answered = ", ".join(answer.id for answer in turn.input)
        return TurnResult(
            history=b"answered",
            reply=[TextPart(text=f"answered {answered}")],
            text=f"answered {answered}",
        )

    return script


DELETE = ToolRequest(id="call-1", name="delete", args={"id": 3}, kind="approval")
LOCATE = ToolRequest(id="call-2", name="locate", args={}, kind="call")


def test_a_continuation_extends_the_turns_assistant_message() -> None:
    _, harness, _ = setup(asks(DELETE))

    async def scenario() -> None:
        session = harness.session()
        await session.prompt("delete note 3", operation_id="ask")
        events = session.watch()
        await anext(events)

        await session.prompt([Approval(id="call-1", approved=True)], operation_id="ok")
        seen = [await anext(events) for _ in range(7)]
        await events.aclose()

        responded = TranscriptMessage(
            id="ask:reply",
            role="assistant",
            parts=[
                TextPart(text="Let me check."),
                ToolPart(
                    tool_call_id="call-1",
                    tool_name="delete",
                    state="approval-responded",
                    input={"id": 3},
                    approval=ToolApproval(id="call-1", approved=True),
                ),
            ],
        )
        final = TranscriptMessage(
            id="ask:reply", role="assistant", parts=[TextPart(text="answered call-1")]
        )
        assert seen == [
            OperationEvent(operation_id="ok", status="queued"),
            MessageEvent(message=responded),
            OperationEvent(operation_id="ok", status="running"),
            RunStartEvent(operations=("ok",)),
            MessageEvent(message=final),
            RunEndEvent(operations=("ok",)),
            OperationEvent(operation_id="ok", status="done"),
        ]
        assert [message.id for message in transcript(harness)] == ["ask", "ask:reply"]
        assert harness.turns[1].input == [Approval(id="call-1", approved=True)]

    asyncio.run(scenario())


def test_partial_answers_wait_for_the_rest_and_the_first_answer_wins() -> None:
    _, harness, _ = setup(asks(DELETE, LOCATE))

    async def scenario() -> None:
        session = harness.session()
        await session.prompt("go")

        first = await session.prompt([Approval(id="call-1", approved=False)])
        again = await session.prompt([Approval(id="call-1", approved=True)])
        assert (first.status, again.status) == ("done", "unanswered")
        assert again.reason == "not-waiting"
        assert len(harness.turns) == 1

        last = await session.prompt([ToolOutput(id="call-2", value={"lat": 1})])
        assert last.text == "answered call-1, call-2"
        assert harness.turns[1].input == [
            Approval(id="call-1", approved=False),
            ToolOutput(id="call-2", value={"lat": 1}),
        ]

        late = await session.prompt([ToolOutput(id="call-2", value=None)])
        assert (late.status, late.reason) == ("unanswered", "not-waiting")

    asyncio.run(scenario())


def test_a_client_tool_answer_from_anyone_but_its_owner_is_refused() -> None:
    _, harness, _ = setup(asks(LOCATE))

    async def scenario() -> None:
        session = harness.session()
        await session.submit("where am I?", operation_id="ask", participant="ann")
        await session.wait("ask")
        [_, reply] = transcript(harness)
        assert isinstance(reply.parts[1], ToolPart)
        assert reply.parts[1].owner == "ann"

        await session.submit(
            [ToolOutput(id="call-2", value="bob's")], operation_id="b", participant="bob"
        )
        refused = await session.wait("b")
        assert (refused.status, refused.reason) == ("unanswered", "not-owner")

        await session.submit(
            [ToolOutput(id="call-2", value="ann's")], operation_id="a", participant="ann"
        )
        assert (await session.wait("a")).status == "done"
        assert harness.turns[1].participant == "ann"

    asyncio.run(scenario())


def test_answers_after_a_new_message_are_not_wanted() -> None:
    ask = asks(DELETE)

    async def asks_once(turn: Turn) -> TurnResult:
        return await (ask if turn.input == "delete note 3" else echo)(turn)

    _, harness, _ = setup(asks_once)

    async def scenario() -> None:
        session = harness.session()
        await session.prompt("delete note 3")
        await session.prompt("never mind")

        late = await session.prompt([Approval(id="call-1", approved=True)])
        assert (late.status, late.reason) == ("unanswered", "not-waiting")

    asyncio.run(scenario())


def test_a_partial_reply_survives_abort() -> None:
    blocker = Blocker()
    _, harness, _ = setup(blocker)

    async def scenario() -> None:
        session = harness.session()
        events = session.watch()
        await anext(events)
        await session.submit("hi", operation_id="op")
        await blocker.started.wait()
        await session.abort("op")

        seen = [await anext(events) for _ in range(8)]
        await events.aclose()
        partial = TranscriptMessage(
            id="op:reply", role="assistant", parts=[TextPart(text="partial")]
        )
        assert seen[-3:] == [
            MessageEvent(message=partial),
            RunEndEvent(operations=("op",)),
            OperationEvent(operation_id="op", status="aborted"),
        ]
        assert transcript(harness)[-1] == partial

    asyncio.run(scenario())


def test_a_partial_reply_survives_a_crash_past_max_restarts() -> None:
    blocker = Blocker()
    _, dead, host = setup(blocker, max_restarts=0)

    async def scenario() -> None:
        await dead.session().submit("hi", operation_id="op")
        await blocker.started.wait()

        lifecycle, harness, _ = setup(host=restarted(host), max_restarts=0)
        await lifecycle.start()
        await lifecycle.alarm()
        result = await harness.session().wait("op")

        assert (result.status, result.reason) == ("unanswered", "interrupted")
        assert transcript(harness)[-1] == TranscriptMessage(
            id="op:reply", role="assistant", parts=[TextPart(text="partial")]
        )
        await dead.dispose()

    asyncio.run(scenario())


def test_a_resumed_turn_extends_what_the_crashed_attempt_saved() -> None:
    blocker = Blocker()
    _, dead, host = setup(blocker)

    async def scenario() -> None:
        await dead.session().submit("hi", operation_id="op")
        await blocker.started.wait()

        resumed = Blocker()
        lifecycle, harness, _ = setup(resumed, host=restarted(host))
        await lifecycle.start()
        await lifecycle.alarm()
        await resumed.started.wait()

        snapshot = await anext(harness.session().watch())
        assert isinstance(snapshot, SessionSnapshot)
        assert [message.id for message in snapshot.messages] == ["op", "op:reply"]
        assert snapshot.run == RunSnapshot(operations=("op",), partial=(TextStart(id="0"),))

        resumed.release.set()
        assert (await harness.session().wait("op")).status == "done"
        await dead.dispose()

    asyncio.run(scenario())


def test_a_snapshot_mid_run_leaves_the_reply_to_the_partial_chunks() -> None:
    blocker = Blocker()
    _, harness, _ = setup(blocker)

    async def scenario() -> None:
        session = harness.session()
        await session.submit("hi", operation_id="op")
        await session.submit("next", operation_id="next")
        await blocker.started.wait()

        snapshot = await anext(session.watch())
        assert snapshot == SessionSnapshot(
            messages=[
                TranscriptMessage(id="op", role="user", parts=[TextPart(text="hi")])
            ],
            pending=await session.pending(),
            run=RunSnapshot(operations=("op",), partial=(TextStart(id="0"),)),
        )
        blocker.release.set()
        await session.wait("next")

    asyncio.run(scenario())


def test_fork_copies_the_transcript_with_the_history() -> None:
    _, harness, _ = setup()

    async def scenario() -> None:
        await harness.prompt("hi")
        fork = await harness.sessions.fork("")

        assert transcript(harness, fork.id) == transcript(harness)
        assert harness._store.history(fork.id) == b"hi;"

    asyncio.run(scenario())


class Seeding(ScriptedHarness):
    def reset_history(self, handoff: str | None) -> bytes | None:
        return None if handoff is None else f"seeded:{handoff}".encode()


def test_reset_clears_history_and_transcript_and_carries_a_handoff() -> None:
    harness = Seeding()
    Lifecycle(Host()).use(harness)

    async def scenario() -> None:
        session = harness.session()
        await harness.prompt("hi")
        events = session.watch()
        await anext(events)

        await session.reset("Use metric units.")
        reset, note = await anext(events), await anext(events)
        await events.aclose()

        assert reset == ResetEvent()
        assert isinstance(note, MessageEvent)
        assert note.message.role == "system"
        assert note.message.parts == [TextPart(text="Use metric units.")]
        assert transcript(harness) == [note.message]
        assert harness._store.history("") == b"seeded:Use metric units."

        await session.reset()
        assert transcript(harness) == []
        assert harness._store.history("") is None

    asyncio.run(scenario())


_put_message = HarnessStore.put_message


def _fails_for_replies(
    store: HarnessStore, session: str, message: TranscriptMessage, *rest: object
) -> None:
    if message.role == "assistant":
        raise RuntimeError("disk full")
    _put_message(store, session, message, *rest)


def test_a_failed_save_writes_neither_checkpoint_nor_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def saves(turn: Turn) -> TurnResult:
        turn.save(b"step-1", reply=[TextPart(text="partial")])
        return await echo(turn)

    _, harness, _ = setup(saves)

    async def scenario() -> None:
        monkeypatch.setattr(HarnessStore, "put_message", _fails_for_replies)
        result = await harness.session().prompt("hi", operation_id="op")

        assert (result.status, result.reason) == ("unanswered", "disk full")
        assert harness._store.get("", "op").checkpoint is None

    asyncio.run(scenario())


def test_a_failed_settlement_writes_neither_history_nor_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, harness, _ = setup()

    async def scenario() -> None:
        await harness.prompt("first")
        monkeypatch.setattr(HarnessStore, "put_message", _fails_for_replies)
        await harness.session().submit("second", operation_id="op")
        for _ in range(5):
            await asyncio.sleep(0)

        assert harness._store.get("", "op").status == "running"
        assert harness._store.history("") == b"first;"
        assert [message.id for message in transcript(harness)][-1] == "op"
        await harness.dispose()

    asyncio.run(scenario())
