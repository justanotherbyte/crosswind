"""The base class for harnesses: an agent loop hosted in a Durable Object.

Python's form of upstream's draft ``AgentHarness`` interface
(``experimental/channels/harness.ts``). Upstream harnesses each write their
own queue, dedupe, wake logic, transcript, and events; here the base class
owns them and a subclass implements one turn. Durability follows upstream's
ThinkHarness: the operations table is the record of open work, and one
Lifecycle job per session (``wake:<session>``) brings the object back to
finish it. The transcript follows upstream's AI SDK harness: one user message
and one assistant message per turn, and a continuation after tool answers
extends the turn's assistant message.
"""

import asyncio
import dataclasses
import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

from ..core.platform_errors import is_platform_failure
from ..core.timing import to_seconds
from ..core.types import Duration
from ..lifecycle.capability import LifecycleCapability
from ..lifecycle.types import JobContext, JobOutcome, MemoryLimitContext, Reschedule
from .errors import (
    OperationNotFoundError,
    SessionBusyError,
    SessionNotFoundError,
)
from .events import EventHub
from .session import HarnessSession, HarnessSessions
from .store import HarnessStore, OperationRow, is_answers
from .tools import collect_tools
from .transcript import (
    FilePart,
    JSONObject,
    MessagePart,
    TextPart,
    ToolApproval,
    ToolPart,
    TranscriptMessage,
)
from .types import (
    ActiveRun,
    Approval,
    ChunkEvent,
    HarnessEvent,
    HarnessInput,
    MessageEvent,
    OperationEvent,
    OperationResult,
    OperationStatus,
    PendingOperation,
    Receipt,
    ResetEvent,
    RunEndEvent,
    RunSnapshot,
    RunStartEvent,
    SessionInfo,
    SessionSnapshot,
    TextInput,
    ToolAnswer,
    ToolOutput,
    ToolRequest,
    ToolSpec,
    Turn,
    TurnResult,
    WhenBusy,
)

__all__ = ("Harness",)

_log = logging.getLogger("agents.harness")

_SCHEMA_VERSION_KEY = "cf_agents:harness_schema_version"
_SCHEMA_VERSION = 1
_WAKE = "wake"
_ROOT = ""


class Harness(LifecycleCapability):
    """Runs an agent loop in a Durable Object, one turn at a time per session.

    Install it like any capability: ``Lifecycle(self).use(harness)`` on a
    plain Durable Object (forward ``alarm`` to the Lifecycle), or
    ``self.use(harness)`` on an `Agent`. The base class stores sessions,
    operations, and each session's transcript, queues and dedupes
    submissions, publishes events, and resumes turns a crash interrupted.
    Subclasses implement `run_turn`.

    Parameters
    ----------
    id
        The capability id; also keys this harness's rows, so two harnesses
        on one object need different ids.
    target
        The object whose ``@tool`` methods are the tools (usually the host).
    max_restarts
        How many times a crash-interrupted turn is resumed before its
        operation settles ``unanswered`` with reason ``"interrupted"``.
    heartbeat
        How often a session with open work is checked; the longest a turn
        waits to resume after a crash when no request wakes the object.

    Raises
    ------
    ValueError
        If ``max_restarts`` is negative or ``heartbeat`` isn't positive.
    """

    def __init__(
        self,
        id: str,
        *,
        target: object | None = None,
        max_restarts: int = 5,
        heartbeat: Duration = 30,
    ) -> None:
        if max_restarts < 0:
            raise ValueError("max_restarts must be 0 or more")
        if to_seconds(heartbeat) <= 0:
            raise ValueError("heartbeat must be positive")

        super().__init__(id)
        self.target = target
        """The object the tools were collected from."""
        self.sessions = HarnessSessions(self)
        """Creating, forking, and listing sessions."""

        self._tools = collect_tools(target) if target is not None else []
        self._max_restarts = max_restarts
        self._heartbeat = timedelta(seconds=to_seconds(heartbeat))
        self._wake_prefix = f"{_WAKE}:{id}:"
        self._store_instance: HarnessStore | None = None
        self._events = EventHub()
        self._drives: dict[str, asyncio.Task[None]] = {}
        self._runs: dict[str, ActiveRun] = {}
        self._waiters: dict[tuple[str, str], list[asyncio.Future[None]]] = {}

    @property
    def tools(self) -> Sequence[ToolSpec]:
        """The target's ``@tool`` methods, bound."""
        return self._tools

    @property
    def _store(self) -> HarnessStore:
        if self._store_instance is None:
            self._store_instance = HarnessStore(self.lifecycle.sql, self.capability_id)

        return self._store_instance

    async def run_turn(self, turn: Turn) -> TurnResult:
        """Run one turn and return the session's new history and the reply.

        Call ``turn.save`` after each step, with the reply's parts so far, so
        a resumed turn (``checkpoint`` set) can skip finished work and an
        interrupted turn keeps its partial reply; call ``turn.emit`` with a
        `ChunkEvent` for each streamed piece of the reply. Raising settles
        the operation ``unanswered`` with the error as reason.
        """
        raise NotImplementedError

    def reset_history(self, handoff: str | None) -> bytes | None:
        """Return the history a session starts from after ``reset``.

        The default is an empty history. Override to carry the handoff note
        into the model's new context; the base class adds the note to the
        transcript as a system message either way.
        """
        return None

    def session(self, id: str = _ROOT) -> HarnessSession:
        """Return a handle on one session (default: the root session)."""
        return HarnessSession(self, id)

    async def prompt(self, input: HarnessInput) -> OperationResult:
        """Submit to the root session and wait for the result."""
        return await self.session().prompt(input)

    async def on_start(self) -> None:
        """Create the tables once, and wake every session with open work."""
        storage = self.lifecycle.storage
        if (await storage.get(_SCHEMA_VERSION_KEY) or 0) < _SCHEMA_VERSION:
            self._store.ensure_tables()
            await storage.put(_SCHEMA_VERSION_KEY, _SCHEMA_VERSION)
        self._store.ensure_session(_ROOT)

        for session in self._store.sessions_with_open_work():
            await self._wake(session)

    async def on_job(self, context: JobContext) -> JobOutcome:
        """Run a session's wake job: resume its open work, or complete."""
        if context.job.fn != _WAKE:
            return None

        session = context.job.id.removeprefix(self._wake_prefix)
        heartbeat = Reschedule(at=datetime.now(UTC) + self._heartbeat)
        if self._is_driving(session):
            return heartbeat
        if not self._store.open(session):
            return None

        self.lifecycle.track_alarm_work(self._ensure_drive(session))
        return heartbeat

    async def on_memory_limit(self, context: MemoryLimitContext) -> None:
        """Settle running turns once the breaker seals; they'd only run out again."""
        if not context.sealed:
            return

        for row in self._store.running():
            self._settle(row.session, row.id, "unanswered", reason="out_of_memory")

    async def dispose(self) -> None:
        """Stop the drives; their turns resume on the next wake."""
        for drive in self._drives.values():
            drive.cancel()

    # Session calls

    async def _submit(
        self,
        session: str,
        input: HarnessInput,
        *,
        operation_id: str | None,
        message_id: str | None,
        participant: str | None,
        when_busy: WhenBusy,
    ) -> Receipt:
        _check_input(input)
        await self.lifecycle.ready()
        operation_id = operation_id or str(uuid.uuid4())

        duplicate = self._store.get(session, operation_id) is not None
        if not duplicate and when_busy == "reject" and self._store.open(session):
            raise SessionBusyError(session)

        accepted = self.lifecycle.storage.transactionSync(
            partial(
                self._insert,
                session,
                operation_id,
                input,
                message_id=message_id,
                participant=participant,
            )
        )
        if accepted:
            self._events.publish(
                session, OperationEvent(operation_id=operation_id, status="queued")
            )

        await self._wake(session)
        self._ensure_drive(session)
        return Receipt(session=session, operation_id=operation_id, accepted=accepted)

    def _insert(
        self,
        session: str,
        operation_id: str,
        input: HarnessInput,
        *,
        message_id: str | None,
        participant: str | None,
    ) -> bool:
        self._store.ensure_session(session)
        return self._store.insert(
            session,
            operation_id,
            input,
            message_id=message_id,
            participant=participant,
        )

    async def _wait(self, session: str, operation_id: str) -> OperationResult:
        await self.lifecycle.ready()

        while True:
            row = self._store.get(session, operation_id)
            if row is None:
                raise OperationNotFoundError(session, operation_id)
            if not row.is_open:
                return row.result()

            waiter = asyncio.get_running_loop().create_future()
            self._waiters.setdefault((session, operation_id), []).append(waiter)
            await waiter

    async def _pending(self, session: str) -> list[PendingOperation]:
        await self.lifecycle.ready()

        return [row.pending() for row in self._store.open(session)]

    async def _watch(
        self, session: str
    ) -> tuple[asyncio.Queue[HarnessEvent], SessionSnapshot]:
        await self.lifecycle.ready()

        # Subscribe and read in one step, so no event lands in both.
        return self._events.subscribe(session), self._snapshot(session)

    def _snapshot(self, session: str) -> SessionSnapshot:
        messages = self._store.messages(session)
        pending = [row.pending() for row in self._store.open(session)]
        run = self._runs.get(session)
        if run is None:
            return SessionSnapshot(messages=messages, pending=pending)

        # The run's reply so far is in run.partial; show its message as it
        # was before the run, or not at all.
        shown = [
            run.before if message.id == run.message_id else message
            for message in messages
            if message.id != run.message_id or run.before is not None
        ]
        return SessionSnapshot(
            messages=[message for message in shown if message is not None],
            pending=pending,
            run=RunSnapshot(operations=(run.operation_id,), partial=tuple(run.partial)),
        )

    async def _abort(self, session: str, operation_id: str | None) -> None:
        await self.lifecycle.ready()

        rows = [
            row
            for row in self._store.open(session)
            if operation_id is None or row.id == operation_id
        ]
        # Stop the turn first, so its run ends before the operation settles.
        drive = self._drives.get(session)
        if drive is not None and any(row.status == "running" for row in rows):
            drive.cancel()
            await asyncio.wait([drive])

        for row in rows:
            self._settle(session, row.id, "aborted")

        if self._store.open(session):
            self._ensure_drive(session)

    async def _retry(self, session: str, operation_id: str) -> None:
        await self.lifecycle.ready()

        row = self._store.get(session, operation_id)
        if row is None:
            raise OperationNotFoundError(session, operation_id)
        if row.status != "unanswered":
            raise ValueError(
                f"Only unanswered operations can be retried; {operation_id!r} "
                f"is {row.status}"
            )
        # Its checkpoint predates the later turns; resuming it would drop them.
        if self._store.ran_after(session, operation_id):
            raise ValueError(
                f"{operation_id!r} can't be retried after later operations ran"
            )

        self._store.reopen(session, operation_id)
        self._events.publish(
            session, OperationEvent(operation_id=operation_id, status="queued")
        )

        await self._wake(session)
        self._ensure_drive(session)

    async def _reset(self, session: str, handoff: str | None) -> None:
        await self.lifecycle.ready()

        if self._store.open(session):
            raise SessionBusyError(session)

        history = self.reset_history(handoff)
        note = (
            TranscriptMessage(
                id=str(uuid.uuid4()), role="system", parts=[TextPart(text=handoff)]
            )
            if handoff
            else None
        )
        self.lifecycle.storage.transactionSync(
            partial(self._write_reset, session, history, note)
        )

        self._events.publish(session, ResetEvent())
        if note is not None:
            self._events.publish(session, MessageEvent(message=note))

    def _write_reset(
        self, session: str, history: bytes | None, note: TranscriptMessage | None
    ) -> None:
        self._store.ensure_session(session)
        self._store.set_history(session, history)
        self._store.delete_messages(session)
        if note is not None:
            self._store.put_message(session, note, None)

    async def _create_session(self) -> HarnessSession:
        await self.lifecycle.ready()

        id = str(uuid.uuid4())
        self._store.ensure_session(id)
        return HarnessSession(self, id)

    async def _fork_session(self, source: str) -> HarnessSession:
        await self.lifecycle.ready()

        if not self._store.has_session(source):
            raise SessionNotFoundError(source)

        id = str(uuid.uuid4())
        self.lifecycle.storage.transactionSync(
            partial(self._store.fork_session, source, id)
        )
        return HarnessSession(self, id)

    async def _list_sessions(self) -> list[SessionInfo]:
        await self.lifecycle.ready()

        return self._store.sessions()

    def _unsubscribe(self, session: str, queue: asyncio.Queue[HarnessEvent]) -> None:
        self._events.unsubscribe(session, queue)

    def _subscribe(self, session: str) -> asyncio.Queue[HarnessEvent]:
        return self._events.subscribe(session)

    # Driving a session's queue

    async def _wake(self, session: str) -> None:
        await self.lifecycle.jobs.push(
            id=self._wake_prefix + session,
            fn=_WAKE,
            time=datetime.now(UTC),
            singleflight=True,
            recovery_loop=True,
        )

    def _is_driving(self, session: str) -> bool:
        drive = self._drives.get(session)
        return drive is not None and not drive.done()

    def _ensure_drive(self, session: str) -> asyncio.Task[None]:
        drive = self._drives.get(session)
        if drive is not None and not drive.done():
            return drive

        drive = asyncio.create_task(self._drive(session))
        self._drives[session] = drive
        drive.add_done_callback(partial(self._drive_done, session))
        return drive

    def _drive_done(self, session: str, drive: asyncio.Task[None]) -> None:
        if self._drives.get(session) is drive:
            del self._drives[session]

        if not drive.cancelled() and (error := drive.exception()) is not None:
            _log.error("Harness drive for %r failed", session, exc_info=error)

    async def _drive(self, session: str) -> None:
        while rows := self._store.open(session):
            started = self._start_turn(rows[0])
            if started is not None:
                await self._run(*started)

    def _start_turn(self, row: OperationRow) -> tuple[Turn, ActiveRun] | None:
        interrupted = row.status == "running"
        if interrupted and row.attempts >= self._max_restarts:
            self._settle(row.session, row.id, "unanswered", reason="interrupted")
            return None

        attempt = row.attempts + 1 if interrupted else row.attempts
        if is_answers(row.input):
            return self._start_continuation(row, attempt)
        return self._start_prompt(row, attempt)

    def _start_prompt(self, row: OperationRow, attempt: int) -> tuple[Turn, ActiveRun]:
        # Written when the operation starts, not when it's queued, so queued
        # input never lands between a running turn's messages.
        message = TranscriptMessage(
            id=row.message_id or row.id, role="user", parts=_user_parts(row.input)
        )
        self.lifecycle.storage.transactionSync(
            partial(self._write_start, row, attempt, message)
        )
        if row.status == "queued":
            self._events.publish(row.session, MessageEvent(message=message))
            self._events.publish(
                row.session, OperationEvent(operation_id=row.id, status="running")
            )

        return self._begin(
            row,
            attempt,
            input=row.input,
            participant=row.participant,
            message_id=f"{row.id}:reply",
        )

    def _start_continuation(
        self, row: OperationRow, attempt: int
    ) -> tuple[Turn, ActiveRun] | None:
        asking = self._store.asking(row.session, before=row.seq)
        if row.answers is None:
            answers = self._place_answers(row, asking, attempt)
            if answers is None:
                return None
        elif asking is None:
            self._settle(row.session, row.id, "unanswered", reason="not-waiting")
            return None
        else:
            # Placed by an earlier attempt, or retried after one.
            answers = self._store.answers_after(row.session, asking.seq)
            self.lifecycle.storage.transactionSync(
                partial(self._write_start, row, attempt, None)
            )
            if row.status == "queued":
                self._events.publish(
                    row.session, OperationEvent(operation_id=row.id, status="running")
                )

        assert asking is not None
        latest = self._store.latest_assistant_message(row.session)
        return self._begin(
            row,
            attempt,
            input=answers,
            participant=self._store.turn_owner(row.session, asking.seq),
            message_id=latest.id if latest is not None else f"{row.id}:reply",
        )

    def _place_answers(
        self, row: OperationRow, asking: OperationRow | None, attempt: int
    ) -> list[ToolAnswer] | None:
        """Record an answer operation's answers.

        Returns every answer so far once each request has one; otherwise
        settles the operation and returns ``None``.
        """
        if asking is None:
            self._settle(row.session, row.id, "unanswered", reason="not-waiting")
            return None

        requests = {request.id: request for request in asking.requests}
        earlier = self._store.answers_after(row.session, asking.seq)
        answered = {answer.id for answer in earlier}
        owner = self._store.turn_owner(row.session, asking.seq)
        accepted: list[ToolAnswer] = []
        given = row.input
        assert is_answers(given)
        for answer in given:
            request = requests.get(answer.id)
            # The first answer wins.
            if request is None or answer.id in answered:
                continue
            if request.kind != ("approval" if isinstance(answer, Approval) else "call"):
                continue
            # Only the client of the participant who started the turn runs
            # its client tools.
            if (
                isinstance(answer, ToolOutput)
                and owner is not None
                and row.participant is not None
                and row.participant != owner
            ):
                self._settle(row.session, row.id, "unanswered", reason="not-owner")
                return None
            accepted.append(answer)
            answered.add(answer.id)

        if not accepted:
            self._settle(row.session, row.id, "unanswered", reason="not-waiting")
            return None

        complete = answered >= requests.keys()
        latest = self._store.latest_assistant_message(row.session)
        message = None if latest is None else _with_answers(latest, accepted)
        self.lifecycle.storage.transactionSync(
            partial(self._write_answers, row, attempt, accepted, message, complete)
        )
        if message is not None:
            self._events.publish(row.session, MessageEvent(message=message))
        if not complete:
            self._notify_settled(row.session, row.id, "done")
            return None

        self._events.publish(
            row.session, OperationEvent(operation_id=row.id, status="running")
        )
        return [*earlier, *accepted]

    def _write_start(
        self, row: OperationRow, attempt: int, message: TranscriptMessage | None
    ) -> None:
        self._store.start(row.session, row.id, attempts=attempt)
        if message is not None:
            self._store.put_message(row.session, message, row.id)

    def _write_answers(
        self,
        row: OperationRow,
        attempt: int,
        answers: Sequence[ToolAnswer],
        message: TranscriptMessage | None,
        complete: bool,
    ) -> None:
        self._store.record_answers(row.session, row.id, answers)
        if message is not None:
            self._store.put_message(row.session, message, row.id)
        if complete:
            self._store.start(row.session, row.id, attempts=attempt)
        else:
            self._store.settle(
                row.session, row.id, "done", text=None, requests=(), reason=None
            )

    def _begin(
        self,
        row: OperationRow,
        attempt: int,
        *,
        input: HarnessInput,
        participant: str | None,
        message_id: str,
    ) -> tuple[Turn, ActiveRun]:
        run = ActiveRun(
            operation_id=row.id,
            message_id=message_id,
            before=self._store.message(row.session, message_id),
        )
        turn = Turn(
            session=row.session,
            operation_id=row.id,
            input=input,
            history=self._store.history(row.session),
            checkpoint=row.checkpoint,
            attempt=attempt,
            tools=self._tools,
            participant=participant,
            emit=partial(self._emit, row.session, run),
            save=partial(self._save, row.session, run),
        )
        return turn, run

    def _emit(self, session: str, run: ActiveRun, event: HarnessEvent) -> None:
        if isinstance(event, ChunkEvent):
            run.partial.append(event.chunk)
        self._events.publish(session, event)

    def _save(
        self,
        session: str,
        run: ActiveRun,
        checkpoint: bytes,
        /,
        *,
        reply: Sequence[MessagePart] | None = None,
        metadata: JSONObject | None = None,
    ) -> None:
        message = (
            None
            if reply is None
            else TranscriptMessage(
                id=run.message_id,
                role="assistant",
                parts=list(reply),
                metadata=metadata,
            )
        )
        saved = self.lifecycle.storage.transactionSync(
            partial(self._write_checkpoint, session, run, checkpoint, message)
        )
        if saved and message is not None:
            run.wrote_reply = True

    def _write_checkpoint(
        self,
        session: str,
        run: ActiveRun,
        checkpoint: bytes,
        message: TranscriptMessage | None,
    ) -> bool:
        if not self._store.save_checkpoint(session, run.operation_id, checkpoint):
            return False

        if message is not None:
            self._store.put_message(session, message, run.operation_id)
        return True

    async def _run(self, turn: Turn, run: ActiveRun) -> None:
        self._runs[turn.session] = run
        self._events.publish(
            turn.session, RunStartEvent(operations=(turn.operation_id,))
        )
        try:
            result = await self.lifecycle.run_in_host_context(
                partial(self.run_turn, turn)
            )
        except Exception as error:
            if is_platform_failure(error):
                raise

            _log.exception("Harness turn %r failed", turn.operation_id)
            self._settle(
                turn.session,
                turn.operation_id,
                "unanswered",
                reason=str(error) or type(error).__name__,
            )
            return
        else:
            self._settle(
                turn.session,
                turn.operation_id,
                result.status,
                text=result.text,
                requests=result.requests,
                history=result.history,
                reply=result.reply,
                metadata=result.metadata,
            )
        finally:
            self._end_run(turn.session, run)

    def _end_run(self, session: str, run: ActiveRun) -> None:
        if self._runs.get(session) is not run:
            return

        del self._runs[session]
        if run.wrote_reply and (
            message := self._store.message(session, run.message_id)
        ):
            self._events.publish(session, MessageEvent(message=message))
        self._events.publish(session, RunEndEvent(operations=(run.operation_id,)))

    def _settle(
        self,
        session: str,
        operation_id: str,
        status: OperationStatus,
        *,
        text: str | None = None,
        requests: Sequence[ToolRequest] = (),
        reason: str | None = None,
        history: bytes | None = None,
        reply: Sequence[MessagePart] = (),
        metadata: JSONObject | None = None,
    ) -> None:
        run = self._runs.get(session)
        if run is not None and run.operation_id != operation_id:
            run = None
        message = (
            TranscriptMessage(
                id=run.message_id,
                role="assistant",
                parts=list(reply),
                metadata=metadata,
            )
            if run is not None and reply
            else None
        )
        settled = self.lifecycle.storage.transactionSync(
            partial(
                self._write_settlement,
                session,
                operation_id,
                status,
                text=text,
                requests=requests,
                reason=reason,
                history=history,
                message=message,
            )
        )
        if not settled:
            return

        if run is not None:
            run.wrote_reply = run.wrote_reply or message is not None
            self._end_run(session, run)
        self._notify_settled(session, operation_id, status)

    def _write_settlement(
        self,
        session: str,
        operation_id: str,
        status: OperationStatus,
        *,
        history: bytes | None,
        message: TranscriptMessage | None,
        **result: Any,
    ) -> bool:
        if not self._store.settle(session, operation_id, status, **result):
            return False

        if history is not None:
            self._store.set_history(session, history)
        if message is not None:
            self._store.put_message(session, message, operation_id)
        return True

    def _notify_settled(
        self, session: str, operation_id: str, status: OperationStatus
    ) -> None:
        self._events.publish(
            session, OperationEvent(operation_id=operation_id, status=status)
        )
        for waiter in self._waiters.pop((session, operation_id), ()):
            if not waiter.done():
                waiter.set_result(None)


def _check_input(input: HarnessInput) -> None:
    if isinstance(input, str):
        return
    if not input:
        raise ValueError("Input must be a prompt or at least one part or answer")

    answers = [isinstance(item, Approval | ToolOutput) for item in input]
    if any(answers) and not all(answers):
        raise TypeError("Input mixes message parts and tool answers")


def _user_parts(input: HarnessInput) -> list[MessagePart]:
    if isinstance(input, str):
        return [TextPart(text=input)]

    return [
        TextPart(text=part.text)
        if isinstance(part, TextInput)
        else FilePart(media_type=part.media_type, url=part.url, filename=part.filename)
        for part in input
        if not isinstance(part, Approval | ToolOutput)
    ]


def _with_answers(
    message: TranscriptMessage, answers: Sequence[ToolAnswer]
) -> TranscriptMessage | None:
    """Return ``message`` with its tool parts showing ``answers``.

    ``None`` if the message has none of their calls.
    """
    by_id = {answer.id: answer for answer in answers}
    parts: list[MessagePart] = []
    for part in message.parts:
        answer = by_id.get(part.tool_call_id) if isinstance(part, ToolPart) else None
        if not isinstance(part, ToolPart) or answer is None:
            parts.append(part)
        elif isinstance(answer, Approval):
            parts.append(
                dataclasses.replace(
                    part,
                    state="approval-responded",
                    approval=ToolApproval(
                        id=part.approval.id if part.approval else answer.id,
                        approved=answer.approved,
                        reason=answer.message,
                    ),
                )
            )
        else:
            parts.append(
                dataclasses.replace(part, state="output-available", output=answer.value)
            )

    if parts == message.parts:
        return None
    return dataclasses.replace(message, parts=parts)
