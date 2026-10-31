"""Upstream ``chat/submit-concurrency.ts``.

The controller only decides and keeps counts. The agent applies the decision.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from .types import Debounce, MessageConcurrency

__all__ = ("SubmitConcurrencyController", "SubmitDecision")

_IDLE_POLL_SECONDS = 0.005


@dataclass(slots=True, frozen=True)
class SubmitDecision:
    action: Literal["execute", "drop"]
    strategy: MessageConcurrency | None = None
    submit_sequence: int | None = None
    debounce_until: float | None = None


class SubmitConcurrencyController:
    __slots__ = (
        "_debounce_waiters",
        "_latest_overlapping",
        "_pending_enqueues",
        "_reset_epoch",
        "_submit_sequence",
    )

    def __init__(self) -> None:
        self._submit_sequence = 0
        self._latest_overlapping = 0
        self._pending_enqueues = 0
        self._reset_epoch = 0
        self._debounce_waiters: set[asyncio.Future[None]] = set()

    @property
    def pending_enqueue_count(self) -> int:
        return self._pending_enqueues

    def decide(
        self,
        concurrency: MessageConcurrency,
        *,
        is_submit_message: bool,
        queued_turns: int,
    ) -> SubmitDecision:
        if not is_submit_message or queued_turns + self._pending_enqueues == 0:
            return SubmitDecision("execute")
        if concurrency == "drop":
            return SubmitDecision("drop", concurrency)
        if concurrency == "queue":
            return SubmitDecision("execute", concurrency)
        self._submit_sequence += 1
        sequence = self._latest_overlapping = self._submit_sequence
        if isinstance(concurrency, Debounce):
            until = asyncio.get_running_loop().time() + concurrency.seconds
            return SubmitDecision("execute", concurrency, sequence, until)
        return SubmitDecision("execute", concurrency, sequence)

    # Callers must release once the send reaches the turn queue or is abandoned.
    def begin_enqueue(self) -> Callable[[], None]:
        self._pending_enqueues += 1
        epoch = self._reset_epoch
        released = False

        def release() -> None:
            nonlocal released
            if released:
                return
            released = True
            if self._reset_epoch == epoch:
                self._pending_enqueues = max(0, self._pending_enqueues - 1)

        return release

    def is_superseded(self, submit_sequence: int | None) -> bool:
        return (
            submit_sequence is not None and submit_sequence < self._latest_overlapping
        )

    async def wait_until(self, deadline: float) -> None:
        loop = asyncio.get_running_loop()
        remaining = deadline - loop.time()
        if remaining <= 0:
            return
        waiter: asyncio.Future[None] = loop.create_future()
        handle = loop.call_later(remaining, _resolve, waiter)
        self._debounce_waiters.add(waiter)
        try:
            await waiter
        finally:
            handle.cancel()
            self._debounce_waiters.discard(waiter)

    def cancel_active_debounce(self) -> None:
        for waiter in list(self._debounce_waiters):
            _resolve(waiter)

    def reset(self) -> None:
        self._reset_epoch += 1
        self._pending_enqueues = 0
        self.cancel_active_debounce()

    async def wait_for_idle(
        self, wait_for_queue_idle: Callable[[], Awaitable[None]]
    ) -> None:
        while True:
            await wait_for_queue_idle()
            if self._pending_enqueues == 0:
                return
            await asyncio.sleep(_IDLE_POLL_SECONDS)


def _resolve(future: asyncio.Future[None]) -> None:
    if not future.done():
        future.set_result(None)
