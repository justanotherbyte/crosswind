"""Upstream ``chat/turn-queue.ts``.

A chat clear advances the generation, so turns queued before it never run.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

__all__ = ("TurnQueue", "TurnResult")


@dataclass(slots=True, frozen=True)
class TurnResult[T]:
    status: Literal["completed", "stale"]
    value: T | None = None


class TurnQueue:
    __slots__ = ("_active_request_id", "_counts", "_generation", "_tail")

    def __init__(self) -> None:
        self._tail: asyncio.Future[None] | None = None
        self._generation = 0
        self._active_request_id: str | None = None
        self._counts: dict[int, int] = {}

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def active_request_id(self) -> str | None:
        return self._active_request_id

    @property
    def is_active(self) -> bool:
        return self._active_request_id is not None

    async def enqueue[T](
        self,
        request_id: str,
        fn: Callable[[], Awaitable[T]],
        *,
        generation: int | None = None,
    ) -> TurnResult[T]:
        captured = self._generation if generation is None else generation
        self._counts[captured] = self._counts.get(captured, 0) + 1
        previous = self._tail
        release: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._tail = release
        try:
            if previous is not None:
                # Shielded: cancelling this caller mustn't cancel the turn
                # ahead of it.
                await asyncio.shield(previous)
        except asyncio.CancelledError:
            # The turns behind this one keep their order.
            self._decrement(captured)
            if previous is not None:
                previous.add_done_callback(lambda _: _resolve(release))
            raise
        if self._generation != captured:
            self._decrement(captured)
            _resolve(release)
            return TurnResult("stale")
        self._active_request_id = request_id
        try:
            return TurnResult("completed", await fn())
        finally:
            self._active_request_id = None
            self._decrement(captured)
            _resolve(release)

    def reset(self) -> None:
        self._generation += 1

    async def wait_for_idle(self) -> None:
        # The tail is the last turn queued, so once it's done all are; a turn
        # queued while waiting becomes the new tail.
        while (tail := self._tail) is not None and not tail.done():
            await asyncio.shield(tail)

    def queued_count(self, generation: int | None = None) -> int:
        return self._counts.get(
            self._generation if generation is None else generation, 0
        )

    def _decrement(self, generation: int) -> None:
        count = self._counts.get(generation, 1) - 1
        if count <= 0:
            self._counts.pop(generation, None)
        else:
            self._counts[generation] = count


def _resolve(future: asyncio.Future[None]) -> None:
    if not future.done():
        future.set_result(None)
