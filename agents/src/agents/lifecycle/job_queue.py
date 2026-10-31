"""Port of upstream ``lifecycle/job-queue.ts``.

Storage keeps upstream's epoch milliseconds and JSON field names
(``.design/sql_schemas.md`` §2).
"""

import json
import secrets
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from typing import Any

from ..core.sql import Sql
from ..core.timing import (
    epoch_ms,
    from_epoch_ms,
    to_milliseconds,
    to_seconds,
)
from ..core.types import Duration, JSONValue, RetryOptions
from .types import JobOutcome, JobRow, LifecycleJob

__all__ = (
    "DEFAULT_HUNG_TIMEOUT_SECONDS",
    "HOST_JOB_OWNER",
    "JobQueue",
    "LifecycleJobs",
    "hung_timeout_ms",
    "is_hung",
    "job_from_row",
    "new_job_id",
    "retry_from_json",
    "retry_to_json",
)

HOST_JOB_OWNER = "host"
DEFAULT_HUNG_TIMEOUT_SECONDS = 30

# Same alphabet and length as upstream's nanoid(9).
_ID_ALPHABET = "useandom-26T198340PX75pxJACKVERYMINDBUSHWOLF_GQZbfghjklqvwyzrict"
_ID_LENGTH = 9

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS cf_agents_jobs (
  id TEXT PRIMARY KEY NOT NULL,
  capability TEXT NOT NULL,
  fn TEXT NOT NULL,
  time INTEGER NOT NULL,
  payload TEXT,
  retry_options TEXT,
  singleflight INTEGER NOT NULL DEFAULT 0,
  hung_timeout_seconds INTEGER,
  exclusive INTEGER NOT NULL DEFAULT 0,
  recovery_loop INTEGER NOT NULL DEFAULT 0,
  running INTEGER NOT NULL DEFAULT 0,
  execution_started_at INTEGER,
  created_at INTEGER NOT NULL DEFAULT (unixepoch())
) WITHOUT ROWID"""


def new_job_id() -> str:
    return "".join(secrets.choice(_ID_ALPHABET) for _ in range(_ID_LENGTH))


def retry_to_json(retry: RetryOptions) -> str:
    # Upstream's stored shape: camelCase keys, milliseconds.
    return json.dumps(
        {
            "maxAttempts": retry.max_attempts,
            "baseDelayMs": to_milliseconds(retry.base_delay),
            "maxDelayMs": to_milliseconds(retry.max_delay),
        }
    )


def retry_from_json(text: str) -> RetryOptions:
    raw: dict[str, Any] = json.loads(text)
    defaults = RetryOptions()
    return RetryOptions(
        max_attempts=raw.get("maxAttempts", defaults.max_attempts),
        base_delay=raw["baseDelayMs"] / 1000
        if "baseDelayMs" in raw
        else defaults.base_delay,
        max_delay=raw["maxDelayMs"] / 1000
        if "maxDelayMs" in raw
        else defaults.max_delay,
    )


def job_from_row(row: JobRow) -> LifecycleJob:
    payload = row["payload"]
    retry = row["retry_options"]

    return LifecycleJob(
        id=row["id"],
        capability=row["capability"],
        fn=row["fn"],
        time=from_epoch_ms(row["time"]),
        payload=json.loads(payload) if payload is not None else None,
        retry=retry_from_json(retry) if retry is not None else None,
        singleflight=row["singleflight"] == 1,
        exclusive=row["exclusive"] == 1,
        recovery_loop=row["recovery_loop"] == 1,
        # created_at is epoch seconds (unixepoch() default), as upstream.
        created_at=from_epoch_ms(row["created_at"] * 1000),
    )


def hung_timeout_ms(row: JobRow) -> int:
    seconds = row["hung_timeout_seconds"]
    return (seconds if seconds is not None else DEFAULT_HUNG_TIMEOUT_SECONDS) * 1000


def is_hung(row: JobRow, now_ms: int) -> bool:
    return now_ms - (row["execution_started_at"] or 0) >= hung_timeout_ms(row)


def _require_time(time: datetime) -> int:
    ms = epoch_ms(time)
    if ms < 0:
        raise ValueError(f"invalid job time: {time!r}")
    return ms


class JobQueue:
    __slots__ = ("_sql", "_table_ready")

    def __init__(self, sql: Sql) -> None:
        self._sql = sql
        self._table_ready = False

    def _table_sql(self) -> Sql:
        if not self._table_ready:
            self._sql(_CREATE_TABLE)
            self._table_ready = True
        return self._sql

    def _rows(self, query: Any, *params: Any) -> Sequence[JobRow]:
        return self._table_sql()(query, *params, row=JobRow)

    def push(
        self,
        owner: str,
        *,
        fn: str,
        time: datetime,
        payload: JSONValue = None,
        id: str | None = None,
        retry: RetryOptions | None = None,
        singleflight: bool = False,
        hung_timeout: Duration | None = None,
        exclusive: bool = False,
        recovery_loop: bool = False,
    ) -> LifecycleJob:
        if not fn.strip():
            raise ValueError("jobs require a non-empty fn")

        # Replacing clears the in-flight marker, so this newer intent wins over
        # an outcome a running dispatch returns later.
        job_id = id if id is not None else new_job_id()
        self._rows(
            """INSERT INTO cf_agents_jobs
                 (id, capability, fn, time, payload, retry_options, singleflight,
                  hung_timeout_seconds, exclusive, recovery_loop, running,
                  execution_started_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL)
               ON CONFLICT(id) DO UPDATE SET
                 fn = excluded.fn,
                 time = excluded.time,
                 payload = excluded.payload,
                 retry_options = excluded.retry_options,
                 singleflight = excluded.singleflight,
                 hung_timeout_seconds = excluded.hung_timeout_seconds,
                 exclusive = excluded.exclusive,
                 recovery_loop = excluded.recovery_loop,
                 running = 0,
                 execution_started_at = NULL
               WHERE cf_agents_jobs.capability = excluded.capability""",
            job_id,
            owner,
            fn,
            _require_time(time),
            json.dumps(payload) if payload is not None else None,
            retry_to_json(retry) if retry is not None else None,
            int(singleflight),
            int(to_seconds(hung_timeout)) if hung_timeout is not None else None,
            int(exclusive),
            int(recovery_loop),
        )
        job = self.get(owner, job_id)
        if job is None:
            raise ValueError(self._push_failure(job_id))
        return job

    def _push_failure(self, job_id: str) -> str:
        rows = self._sql("SELECT capability FROM cf_agents_jobs WHERE id = ?", job_id)
        if rows:
            return (
                f"job id {job_id!r} already belongs to {rows[0]['capability']!r}; "
                "job ids are scoped to their owner"
            )
        return f"failed to persist job {job_id!r}"

    def cancel(self, owner: str, id: str) -> bool:
        if self.get(owner, id) is None:
            return False
        self._rows(
            "DELETE FROM cf_agents_jobs WHERE id = ? AND capability = ?", id, owner
        )
        return True

    def reschedule(self, owner: str, id: str, time: datetime) -> bool:
        ms = _require_time(time)
        if self.get(owner, id) is None:
            return False
        self._rows(
            """UPDATE cf_agents_jobs
               SET time = ?, running = 0, execution_started_at = NULL
               WHERE id = ? AND capability = ?""",
            ms,
            id,
            owner,
        )
        return True

    def get(self, owner: str, id: str) -> LifecycleJob | None:
        rows = self._rows(
            "SELECT * FROM cf_agents_jobs WHERE id = ? AND capability = ?", id, owner
        )
        return job_from_row(rows[0]) if rows else None

    def list(self, owner: str) -> Sequence[LifecycleJob]:
        rows = self._rows(
            "SELECT * FROM cf_agents_jobs WHERE capability = ? ORDER BY time ASC",
            owner,
        )
        return [job_from_row(row) for row in rows]

    def due(self, now_ms: int) -> Sequence[JobRow]:
        return self._rows(
            "SELECT * FROM cf_agents_jobs WHERE time <= ? ORDER BY time ASC", now_ms
        )

    def still_due(self, id: str, now_ms: int) -> JobRow | None:
        rows = self._rows(
            "SELECT * FROM cf_agents_jobs WHERE id = ? AND time <= ?", id, now_ms
        )
        return rows[0] if rows else None

    def mark_running(self, id: str, now_ms: int) -> None:
        self._rows(
            """UPDATE cf_agents_jobs SET running = 1, execution_started_at = ?
               WHERE id = ?""",
            now_ms,
            id,
        )

    def clear_running(self, id: str) -> None:
        self._rows(
            """UPDATE cf_agents_jobs SET running = 0, execution_started_at = NULL
               WHERE id = ?""",
            id,
        )

    def delete(self, id: str) -> None:
        self._rows("DELETE FROM cf_agents_jobs WHERE id = ?", id)

    def retime(self, id: str, time_ms: int) -> None:
        self._rows(
            """UPDATE cf_agents_jobs
               SET time = ?, running = 0, execution_started_at = NULL
               WHERE id = ?""",
            time_ms,
            id,
        )

    def delay_recovery_loop_jobs(self, time_ms: int) -> None:
        self._rows(
            """UPDATE cf_agents_jobs
               SET time = ?, running = 0, execution_started_at = NULL
               WHERE recovery_loop = 1 AND time <= ?""",
            time_ms,
            time_ms,
        )

    def recovery_loop_jobs(self) -> Sequence[LifecycleJob]:
        rows = self._rows(
            "SELECT * FROM cf_agents_jobs WHERE recovery_loop = 1 ORDER BY time ASC"
        )
        return [job_from_row(row) for row in rows]

    def purge_recovery_loop_jobs(self) -> None:
        self._rows("DELETE FROM cf_agents_jobs WHERE recovery_loop = 1")

    def apply_outcome(self, id: str, outcome: JobOutcome) -> None:
        # Every dispatch carries running = 1. A same-id push or reschedule during
        # dispatch clears it, and then the WHERE drops this outcome.
        if outcome is None:
            self._rows("DELETE FROM cf_agents_jobs WHERE id = ? AND running = 1", id)
        elif outcome == "yield":
            self.clear_running(id)
        else:
            self._rows(
                """UPDATE cf_agents_jobs
                   SET time = ?, running = 0, execution_started_at = NULL
                   WHERE id = ? AND running = 1""",
                _require_time(outcome.at),
                id,
            )

    def next_alarm_time(self, now_ms: int) -> int | None:
        exclusive = self._earliest_exclusive_time()
        if exclusive is not None:
            return exclusive

        candidates = (
            self._earliest_ready_time(now_ms),
            self._earliest_hung_recheck(now_ms),
        )
        return min((time for time in candidates if time is not None), default=None)

    def _earliest_exclusive_time(self) -> int | None:
        rows = self._table_sql()(
            "SELECT MIN(time) AS time FROM cf_agents_jobs WHERE exclusive = 1"
        )
        return int(rows[0]["time"]) if rows and rows[0]["time"] is not None else None

    def _earliest_ready_time(self, now_ms: int) -> int | None:
        rows = self._table_sql()(
            """SELECT MIN(time) AS time FROM cf_agents_jobs
               WHERE singleflight = 0
                  OR running = 0
                  OR coalesce(execution_started_at, 0)
                     + coalesce(hung_timeout_seconds, ?) * 1000 <= ?""",
            DEFAULT_HUNG_TIMEOUT_SECONDS,
            now_ms,
        )
        if not rows or rows[0]["time"] is None:
            return None
        # Clamped into the future: overdue rows must re-fire.
        return max(int(rows[0]["time"]), now_ms + 1)

    def _earliest_hung_recheck(self, now_ms: int) -> int | None:
        rows = self._table_sql()(
            """SELECT MIN(coalesce(execution_started_at, 0)
                      + coalesce(hung_timeout_seconds, ?) * 1000) AS recheck
               FROM cf_agents_jobs
               WHERE singleflight = 1
                 AND running = 1
                 AND coalesce(execution_started_at, 0)
                     + coalesce(hung_timeout_seconds, ?) * 1000 > ?""",
            DEFAULT_HUNG_TIMEOUT_SECONDS,
            DEFAULT_HUNG_TIMEOUT_SECONDS,
            now_ms,
        )
        return (
            int(rows[0]["recheck"]) if rows and rows[0]["recheck"] is not None else None
        )


class LifecycleJobs:
    """One owner's view of the job queue; every change re-arms the alarm.

    Parameters
    ----------
    queue
        The shared queue.
    owner
        The capability id (or ``"host"``) every operation is scoped to.
    rearm
        Recomputes the physical alarm from queue state.
    """

    __slots__ = ("_owner", "_queue", "_rearm")

    def __init__(
        self, queue: JobQueue, owner: str, rearm: Callable[[], Awaitable[None]]
    ) -> None:
        self._queue = queue
        self._owner = owner
        self._rearm = rearm

    async def push(
        self,
        *,
        fn: str,
        time: datetime,
        payload: JSONValue = None,
        id: str | None = None,
        retry: RetryOptions | None = None,
        singleflight: bool = False,
        hung_timeout: Duration | None = None,
        exclusive: bool = False,
        recovery_loop: bool = False,
    ) -> LifecycleJob:
        """Push one job; a push with an existing id replaces that job.

        Parameters
        ----------
        fn
            The name this owner's ``on_job`` dispatches on.
        time
            When the job is due (timezone-aware).
        payload
            JSON data passed back in the job.
        id
            A stable id; generated when omitted.
        retry
            Dispatch retries (default: 3 attempts, 0.1 s base, 3 s max).
        singleflight
            Skip this job while a previous run of it is still in flight.
        hung_timeout
            When an in-flight single-flight run counts as hung, and a dispatch
            as slow (default 30 s).
        exclusive
            Suppress ordinary alarm candidates while this job is pending.
        recovery_loop
            Put this job under the alarm memory-limit circuit breaker.

        Returns
        -------
        LifecycleJob
            The stored job.
        """
        job = self._queue.push(
            self._owner,
            fn=fn,
            time=time,
            payload=payload,
            id=id,
            retry=retry,
            singleflight=singleflight,
            hung_timeout=hung_timeout,
            exclusive=exclusive,
            recovery_loop=recovery_loop,
        )
        await self._rearm()
        return job

    async def cancel(self, id: str) -> bool:
        """Cancel one job; return whether it existed."""
        cancelled = self._queue.cancel(self._owner, id)
        await self._rearm()
        return cancelled

    async def reschedule(self, id: str, time: datetime) -> bool:
        """Move one job to ``time``; return whether it existed."""
        moved = self._queue.reschedule(self._owner, id, time)
        await self._rearm()
        return moved

    def get(self, id: str) -> LifecycleJob | None:
        """Return one job, or ``None``."""
        return self._queue.get(self._owner, id)

    def list(self) -> Sequence[LifecycleJob]:
        """Return every job this owner holds, earliest first."""
        return self._queue.list(self._owner)

    async def rearm(self) -> None:
        """Recompute the physical alarm without changing the queue.

        Changes re-arm automatically; this recovers a lost alarm for existing
        jobs (e.g. after an idempotent push that changed nothing).
        """
        await self._rearm()
