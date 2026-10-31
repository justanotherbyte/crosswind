from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal, TypedDict

from ..core.types import JSONValue, RetryOptions

__all__ = (
    "ByIdMessage",
    "CronField",
    "DispatchMessage",
    "InsertMessage",
    "InsertResult",
    "ListMessage",
    "Schedule",
    "ScheduleCallback",
    "ScheduleErrorHandler",
    "ScheduleJobPayload",
    "ScheduleRouteMessage",
    "ScheduleTiming",
    "ScheduleType",
    "WireSchedule",
)

type ScheduleType = Literal["scheduled", "delayed", "cron", "interval"]
"""``"scheduled"`` runs once at a time, ``"delayed"`` once after a delay,
``"cron"`` on a cron expression, ``"interval"`` at a fixed interval."""


@dataclass(slots=True, kw_only=True)
class Schedule:
    """One persisted schedule.

    Parameters
    ----------
    id
        The schedule's id.
    callback
        The name of the callback it runs.
    payload
        The data passed to the callback.
    type
        How it runs (`ScheduleType`).
    time
        When it next runs.
    retry
        The retry policy given when it was created, if any.
    delay
        For ``"delayed"``: the delay it was created with.
    cron
        For ``"cron"``: the cron expression.
    interval
        For ``"interval"``: the time between runs.
    """

    id: str
    callback: str
    payload: JSONValue
    type: ScheduleType
    time: datetime
    retry: RetryOptions | None = None
    delay: timedelta | None = None
    cron: str | None = None
    interval: timedelta | None = None


type ScheduleCallback = Callable[[Any, Schedule], Awaitable[Any]]
type ScheduleErrorHandler = Callable[[Exception], Awaitable[None]]


@dataclass(slots=True, kw_only=True, frozen=True)
class ScheduleTiming:
    type: ScheduleType
    time_ms: int
    delay_seconds: float | None = None
    cron: str | None = None
    interval_seconds: float | None = None


@dataclass(slots=True, kw_only=True, frozen=True)
class CronField:
    minimum: int
    maximum: int
    aliases: Mapping[str, str] = field(default_factory=dict)


class ScheduleJobPayload(TypedDict):
    payload: JSONValue
    type: ScheduleType
    # The caller's override as given, for reading the schedule back. The job's
    # own retry policy is resolved against the Scheduler's default.
    retry: str | None
    delay_seconds: float | None
    cron: str | None
    interval_seconds: float | None
    # The owning facet's route address, or None on the root.
    owner_path: str | None
    owner_path_key: str | None


class WireSchedule(TypedDict):
    id: str
    callback: str
    time_ms: int
    job: ScheduleJobPayload


class InsertResult(TypedDict):
    schedule: WireSchedule
    created: bool


class InsertMessage(TypedDict):
    type: Literal["insert"]
    timing: dict[str, Any]
    callback: str
    payload: JSONValue
    retry: str | None
    idempotent: bool | None


class ByIdMessage(TypedDict):
    type: Literal["get", "cancel"]
    id: str


class ListMessage(TypedDict):
    type: Literal["list"]
    id: str | None
    schedule_type: ScheduleType | None
    start_ms: int | None
    end_ms: int | None


class DispatchMessage(TypedDict):
    type: Literal["dispatch"]
    schedule: WireSchedule
    # The job's resolved policy, unlike ScheduleJobPayload.retry.
    retry: str | None


type ScheduleRouteMessage = InsertMessage | ByIdMessage | ListMessage | DispatchMessage
