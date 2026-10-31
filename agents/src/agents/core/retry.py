"""Port of upstream ``retries.ts`` and ``Agent.retry`` (``.design/utilities.md`` §7).

Covers ``tryN``, ``jitterBackoff`` and ``validateRetryOptions``. Retries are in
memory only; durable retries are schedules, queues, and Tasks.
"""

import asyncio
import inspect
import math
import random
from collections.abc import Awaitable, Callable
from datetime import timedelta

from .timing import to_seconds
from .types import Duration, RetryOptions, ShouldRetry

__all__ = ("backoff_delay", "retry", "run_with_retries", "validate_retry_options")


async def retry[T](
    fn: Callable[[int], Awaitable[T]],
    *,
    max_attempts: int = 3,
    base_delay: Duration = 0.1,
    max_delay: Duration = 3.0,
    should_retry: ShouldRetry | None = None,
) -> T:
    """Call ``fn`` until it succeeds, waiting a random backoff between attempts.

    Parameters
    ----------
    fn
        Called once per attempt with the 1-based attempt number; must return
        a fresh awaitable each time (an ``async def`` function or a
        ``functools.partial`` of one, not a coroutine).
    max_attempts
        Total attempts, including the first.
    base_delay
        The backoff's base delay, a ``timedelta`` or seconds.
    max_delay
        The longest wait between attempts, a ``timedelta`` or seconds.
    should_retry
        Called with the error and the next attempt number; returning
        ``False`` stops retrying and re-raises that error. By default every
        ``Exception`` is retried.

    Returns
    -------
    T
        The first successful result.

    Raises
    ------
    Exception
        The last attempt's error, once attempts run out or ``should_retry``
        returns ``False``. Other ``BaseException`` subclasses, such as
        ``asyncio.CancelledError``, propagate immediately.
    TypeError
        If ``fn`` isn't callable, or an option has the wrong type.
    ValueError
        If an option is out of range.
    """
    options = RetryOptions(
        max_attempts=max_attempts, base_delay=base_delay, max_delay=max_delay
    )
    validate_retry_options(options)
    return await run_with_retries(fn, options, should_retry=should_retry)


# Callers run this when options are given, not when a retry first runs, so bad
# values fail at the call that supplied them.
def validate_retry_options(options: RetryOptions) -> None:
    attempts = options.max_attempts
    if isinstance(attempts, bool) or not isinstance(attempts, int):
        raise TypeError(f"max_attempts must be an int, not {type(attempts).__name__}")
    if attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    base = to_seconds(options.base_delay)
    maximum = to_seconds(options.max_delay)
    for name, seconds in (("base_delay", base), ("max_delay", maximum)):
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError(f"{name} must be positive and finite")
    if base > maximum:
        raise ValueError("base_delay must be <= max_delay")


async def run_with_retries[T](
    fn: Callable[[int], Awaitable[T]],
    options: RetryOptions,
    *,
    should_retry: ShouldRetry | None = None,
) -> T:
    # Upstream tryN. Expects options already checked by validate_retry_options.
    if inspect.iscoroutine(fn) or not callable(fn):
        raise TypeError("fn must be a function returning an awaitable, not a coroutine")

    attempt = 1
    while True:
        try:
            return await fn(attempt)
        except Exception as error:
            next_attempt = attempt + 1
            if next_attempt > options.max_attempts or (
                should_retry is not None and not should_retry(error, next_attempt)
            ):
                raise
            await asyncio.sleep(backoff_delay(attempt, options).total_seconds())
            attempt = next_attempt


def backoff_delay(attempt: int, options: RetryOptions) -> timedelta:
    # "Full jitter", as upstream's jitterBackoff.
    # 2.0 ** 1024 overflows a float; by then the window is capped at max anyway.
    upper = min(
        to_seconds(options.base_delay) * 2.0 ** min(attempt, 1023),
        to_seconds(options.max_delay),
    )
    return timedelta(seconds=upper * random.random())
