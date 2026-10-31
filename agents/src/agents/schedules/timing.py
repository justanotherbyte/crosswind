"""Port of upstream ``schedules/schedule-timing.ts``."""

import math
from datetime import datetime, timedelta

from ..core.timing import epoch_ms, from_epoch_ms, to_seconds
from .cron import parse_cron
from .types import ScheduleTiming

__all__ = (
    "MAX_INTERVAL",
    "is_recurring",
    "next_cron_ms",
    "parse_interval",
    "parse_when",
)

MAX_INTERVAL = timedelta(days=30)


def parse_when(when: datetime | timedelta | float | str, now_ms: int) -> ScheduleTiming:
    if isinstance(when, datetime):
        return ScheduleTiming(type="scheduled", time_ms=epoch_ms(when))
    if isinstance(when, str):
        return ScheduleTiming(
            type="cron", time_ms=next_cron_ms(when, now_ms), cron=when
        )
    if isinstance(when, timedelta) or (
        isinstance(when, int | float) and not isinstance(when, bool)
    ):
        seconds = to_seconds(when)
        if not math.isfinite(seconds):
            raise ValueError("A schedule delay must be finite")
        return ScheduleTiming(
            type="delayed",
            time_ms=now_ms + round(seconds * 1000),
            delay_seconds=seconds,
        )
    raise TypeError(
        "when must be a datetime, a timedelta or number of seconds, or a cron "
        f"string, not {type(when).__name__}"
    )


def parse_interval(interval: timedelta | float, now_ms: int) -> ScheduleTiming:
    seconds = to_seconds(interval)
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("A schedule interval must be positive")
    if seconds > MAX_INTERVAL.total_seconds():
        raise ValueError("A schedule interval can't exceed 30 days")
    return ScheduleTiming(
        type="interval",
        time_ms=now_ms + round(seconds * 1000),
        interval_seconds=seconds,
    )


def next_cron_ms(cron: str, now_ms: int) -> int:
    return epoch_ms(parse_cron(cron).next_after(from_epoch_ms(now_ms)))


# Recurring timings deduplicate by default.
def is_recurring(timing: ScheduleTiming) -> bool:
    return timing.type in ("cron", "interval")
