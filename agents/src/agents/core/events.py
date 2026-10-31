"""Port of upstream `packages/agents/src/core/events.ts`.

Design notes: `.design/core_disposable_store.md` §5.4.
"""

import inspect
import logging
from collections.abc import Callable

__all__ = ("Disposable", "Emitter")

_log = logging.getLogger(__name__)


class Disposable:
    """A handle that releases one resource. Calling `dispose()` again does nothing."""

    __slots__ = ("_release",)

    def __init__(self, release: Callable[[], object]) -> None:
        self._release: Callable[[], object] | None = release

    def dispose(self) -> None:
        """Release the resource; calls after the first do nothing."""
        release, self._release = self._release, None
        if release is not None:
            release()


# A listener that raises is logged and the others still run, so one bad listener
# can't break the code that fires. Owners keep the emitter private and expose
# only `subscribe` (`self.on_change = self._on_change.subscribe`).
class Emitter[T]:
    __slots__ = ("_listeners",)

    def __init__(self) -> None:
        # Insertion-ordered; a token per subscription lets one callable subscribe twice.
        self._listeners: dict[object, Callable[[T], object]] = {}

    def subscribe(self, listener: Callable[[T], object]) -> Disposable:
        token = object()
        self._listeners[token] = listener
        return Disposable(lambda: self._listeners.pop(token, None))

    def fire(self, value: T) -> None:
        # Copy: a listener may unsubscribe (itself or others) while firing.
        for listener in list(self._listeners.values()):
            try:
                result = listener(value)
            except Exception:
                _log.exception("Emitter listener failed")
                continue
            if inspect.isawaitable(result):
                if inspect.iscoroutine(result):
                    result.close()  # don't leave a never-awaited coroutine behind
                raise TypeError("fire() got an async listener; use fire_async()")

    async def fire_async(self, value: T) -> None:
        for listener in list(self._listeners.values()):
            try:
                result = listener(value)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                _log.exception("Emitter listener failed")

    def dispose(self) -> None:
        self._listeners.clear()
