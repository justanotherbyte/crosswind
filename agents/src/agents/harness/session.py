"""Handles on a harness's sessions, matching upstream's ``AgentHarness`` names."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import TYPE_CHECKING

from .types import (
    HarnessEvent,
    HarnessInput,
    MessageEvent,
    OperationEvent,
    OperationResult,
    PendingOperation,
    Receipt,
    ResetEvent,
    RunEndEvent,
    RunStartEvent,
    SessionInfo,
    SessionSnapshot,
    WhenBusy,
)

if TYPE_CHECKING:
    from .harness import Harness

__all__ = ("HarnessSession", "HarnessSessions")


class HarnessSession:
    """One conversation: its history and its operations. No I/O until called.

    Parameters
    ----------
    harness
        The owning harness.
    id
        The session id (``""`` is the root session).
    """

    __slots__ = ("_harness", "id")

    def __init__(self, harness: Harness, id: str) -> None:
        self._harness = harness
        self.id = id

    def __repr__(self) -> str:
        return f"<HarnessSession id={self.id!r}>"

    async def submit(
        self,
        input: HarnessInput,
        *,
        operation_id: str | None = None,
        message_id: str | None = None,
        participant: str | None = None,
        when_busy: WhenBusy = "queue",
    ) -> Receipt:
        """Durably queue a user message or tool answers; returns before the turn runs.

        Parameters
        ----------
        input
            A prompt, message parts, or answers to a ``needs_input`` result's
            requests. Answers may arrive over several operations; the turn
            continues once every request has one, and the first answer to a
            request wins.
        operation_id
            Dedupes retries: submitting an existing id stores nothing new and
            returns a receipt with ``accepted=False``. Generated when omitted.
        message_id
            The id of the user message this places in the transcript; the
            operation id when omitted.
        participant
            Who sent it. The client tool calls of a turn belong to the
            participant who started it: a `ToolOutput` from anyone else
            settles ``unanswered`` with reason ``"not-owner"``.
        when_busy
            ``"queue"`` behind open work, or ``"reject"`` it.

        Raises
        ------
        SessionBusyError
            If ``when_busy="reject"`` and the session has open work.
        TypeError
            If ``input`` mixes message parts and tool answers.
        ValueError
            If ``input`` is an empty sequence.
        """
        return await self._harness._submit(
            self.id,
            input,
            operation_id=operation_id,
            message_id=message_id,
            participant=participant,
            when_busy=when_busy,
        )

    async def wait(self, operation_id: str) -> OperationResult:
        """Wait for an operation to settle and return its result.

        Raises
        ------
        OperationNotFoundError
            If the session has no such operation.
        """
        return await self._harness._wait(self.id, operation_id)

    async def prompt(
        self, input: HarnessInput, *, operation_id: str | None = None
    ) -> OperationResult:
        """Submit and wait for the result."""
        receipt = await self.submit(input, operation_id=operation_id)

        return await self.wait(receipt.operation_id)

    async def pending(self) -> Sequence[PendingOperation]:
        """Return the open operations, oldest first."""
        return await self._harness._pending(self.id)

    async def abort(self, operation_id: str | None = None) -> None:
        """Stop one open operation, or all of them; queued work behind runs."""
        await self._harness._abort(self.id, operation_id)

    async def retry(self, operation_id: str) -> None:
        """Run an ``unanswered`` operation again, from its last checkpoint.

        Raises
        ------
        OperationNotFoundError
            If the session has no such operation.
        ValueError
            If the operation isn't ``unanswered``, or a later one has run.
        """
        await self._harness._retry(self.id, operation_id)

    async def reset(self, handoff: str | None = None) -> None:
        """Start the session over: clear its history and transcript.

        Watchers get a `ResetEvent`. A ``handoff`` note becomes the new
        transcript's first message (a system message), and
        `Harness.reset_history` decides the new history.

        Raises
        ------
        SessionBusyError
            If the session has open work.
        """
        await self._harness._reset(self.id, handoff)

    async def watch(self) -> AsyncIterator[HarnessEvent]:
        """Yield a `SessionSnapshot`, then every event, in order.

        Cancel the iterating task, or close the iterator, to stop watching.
        """
        queue, snapshot = await self._harness._watch(self.id)
        try:
            yield snapshot
            while True:
                yield await queue.get()
        finally:
            self._harness._unsubscribe(self.id, queue)

    async def stream(
        self, input: HarnessInput, *, operation_id: str | None = None
    ) -> AsyncIterator[HarnessEvent]:
        """Submit, then yield this operation's events until it settles.

        Resubmitting an ``operation_id`` that already settled yields nothing;
        call `wait` for its result.
        """
        # Subscribe first: the turn may emit before submit returns.
        queue = self._harness._subscribe(self.id)
        try:
            receipt = await self.submit(input, operation_id=operation_id)
            operation_id = receipt.operation_id
            if not receipt.accepted and all(
                pending.operation_id != operation_id for pending in await self.pending()
            ):
                return

            while True:
                event = await queue.get()
                if not _concerns(event, operation_id):
                    continue

                yield event
                if isinstance(event, OperationEvent) and event.status not in (
                    "queued",
                    "running",
                ):
                    return
        finally:
            self._harness._unsubscribe(self.id, queue)


class HarnessSessions:
    """Creating, forking, and listing a harness's sessions."""

    __slots__ = ("_harness",)

    def __init__(self, harness: Harness) -> None:
        self._harness = harness

    async def create(self) -> HarnessSession:
        """Create an empty session."""
        return await self._harness._create_session()

    async def fork(self, id: str) -> HarnessSession:
        """Create a session with a copy of session ``id``'s history and transcript.

        Raises
        ------
        SessionNotFoundError
            If session ``id`` doesn't exist.
        """
        return await self._harness._fork_session(id)

    async def list(self) -> Sequence[SessionInfo]:
        """Return every session, oldest first."""
        return await self._harness._list_sessions()


def _concerns(event: HarnessEvent, operation_id: str) -> bool:
    match event:
        case RunStartEvent(operations=operations) | RunEndEvent(operations=operations):
            return operation_id in operations
        case SessionSnapshot() | MessageEvent() | ResetEvent():
            return False
        case _:
            return event.operation_id == operation_id
