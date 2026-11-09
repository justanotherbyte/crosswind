---
title: "Add your own methods and data to a Python Container class"
description: "Extend a containers-py Container subclass with RPC methods, its own SQL table, hooks that record results and a schedule that keeps a batch job awake."
---

A `Container` subclass is an ordinary Durable Object class, so you can give
it more than ports and timeouts. Add methods the Worker calls over RPC, keep
your own records in the Durable Object's storage, and use the hooks and
`schedule()` to react to the container's life.

This guide builds a report job. The Worker asks for a report for a date, the
class starts a container that writes it and exits, and the class records how
each run ended. If you haven't yet, [How a container is
managed](architecture.md) explains the alarm, the stored state and the idle
timer that this guide works with.

## Write the class

The container image runs `report.py`, which reads `REPORT_DATE`, does its
work and exits. It serves no HTTP, so the class starts it with `start()`
rather than sending it requests:

```python title="src/entry.py"
import time
from typing import Any, TypedDict

from containers import Container, Schedule

CHECK_IN_EVERY_S = 300
MAX_RUNTIME_S = 2 * 60 * 60


class ReportRun(TypedDict):
    report_date: str
    started_at: int
    exit_code: int | None


class ReportJob(Container):
    entrypoint = ["python", "report.py"]
    enable_internet = False
    sleep_after = "15m"

    def __init__(self, ctx: Any, env: Any) -> None:
        super().__init__(ctx, env)
        self.ctx.storage.sql.exec(
            """CREATE TABLE IF NOT EXISTS report_runs (
                 report_date TEXT PRIMARY KEY,
                 started_at INTEGER NOT NULL,
                 exit_code INTEGER
               )"""
        )

    async def run(self, report_date: str) -> bool:
        state = await self.get_state()
        if state["status"] in ("running", "healthy"):
            return False

        self.ctx.storage.sql.exec(
            "INSERT OR REPLACE INTO report_runs VALUES (?, ?, NULL)",
            report_date,
            int(time.time()),
        )
        await self.start(env_vars={"REPORT_DATE": report_date})
        if not await self.list_schedules("check_in"):
            await self.schedule(CHECK_IN_EVERY_S, "check_in", report_date)
        return True

    async def check_in(self, report_date: str, schedule: Schedule[str]) -> None:
        state = await self.get_state()
        if state["status"] not in ("running", "healthy"):
            return

        rows = list(
            self.ctx.storage.sql.exec(
                "SELECT started_at FROM report_runs WHERE report_date = ?",
                report_date,
            )
        )
        if rows and time.time() - rows[0]["started_at"] > MAX_RUNTIME_S:
            await self.stop()
            return

        self.renew_activity_timeout()
        await self.schedule(CHECK_IN_EVERY_S, "check_in", report_date)

    async def on_stop(self, *, exit_code: int, reason: str) -> None:
        self.ctx.storage.sql.exec(
            "UPDATE report_runs SET exit_code = ? WHERE exit_code IS NULL",
            exit_code,
        )

    async def last_run(self) -> ReportRun | None:
        rows = list(
            self.ctx.storage.sql.exec(
                "SELECT * FROM report_runs ORDER BY started_at DESC LIMIT 1"
            )
        )
        if not rows:
            return None
        row = rows[0]
        return {
            "report_date": row["report_date"],
            "started_at": row["started_at"],
            "exit_code": row["exit_code"],
        }
```

`run()` and `last_run()` are the class's API for the Worker. `run()`
refuses to start a second report while one is going, records the run, then
starts the container with that date. `on_stop()` fills in the exit code once
the SDK sees the process exit. `check_in()` is a `schedule()` callback,
explained [below](#keep-the-container-awake-for-your-own-work).

The `UPDATE` in `on_stop()` only touches rows that have no exit code yet, so
running it twice changes nothing. That matters, because the SDK calls the hook
again if it raises. See [The stored state](architecture.md#the-stored-state).

## Call it from the Worker

Get a stub with [`get_container`](routing.md#one-container-per-entity) and
call the methods on it. The Durable Object called `nightly` holds every run's
record, because they all go through one instance:

```python title="src/entry.py"
from datetime import date

from containers import get_container
from workers import Request, Response, WorkerEntrypoint


class Default(WorkerEntrypoint):
    async def fetch(self, request: Request) -> Response:
        job = get_container(self.env.REPORT_JOB, "nightly")

        if request.method == "POST":
            started = await job.run(date.today().isoformat())
            return Response.from_json({"started": started})

        return Response.from_json(await job.last_run())
```

The Wrangler config is the one from [Getting
started](getting-started.md#4-configure-wrangler), with the class and binding
renamed to `ReportJob` and `REPORT_JOB`. The `migrations` entry must list
the class in `new_sqlite_classes`, because both the SDK and `report_runs` use
SQLite storage.

Methods called through a stub follow the RPC rules in [Calling container
methods](routing.md#calling-container-methods-from-a-worker): define them with
`async def`, pass arguments positionally, and return values that can cross
RPC, such as the plain dict `last_run()` builds. Don't return the SQL rows
themselves.

## Keep the container awake for your own work

The idle timer only sees requests that go through `fetch()` or
`container_fetch()`. A job like this one sends none, so after `sleep_after`
the SDK would call `on_activity_expired()`, which sends `SIGTERM` and stops
the report partway.

`check_in()` prevents that. It runs every five minutes while the container
is up, renews the timer with `renew_activity_timeout()`, and schedules its
next run. When the alarm fires, the SDK runs due tasks before it checks the
timer, so a check-in that renews it in time keeps the container awake. Keep
the interval well below `sleep_after`.

The check-in also gives the job a ceiling. After `MAX_RUNTIME_S` it stops the
container instead of renewing, and once the container has stopped, the next
check-in returns without scheduling another.

`run()` checks `list_schedules("check_in")` before it schedules, so calling
it again doesn't stack up a second chain of check-ins.

## Store your own data

`self.ctx.storage` belongs to your class as much as to the SDK. Create your
own SQL tables, as `report_runs` does, and use key-value storage for small
values. With `crosswind-workers`, the key-value methods are typed. See
[Store data](../workers/durable-objects.md#store-data).

The SDK keeps its own records in the same storage, under the names listed in
[What the SDK stores](architecture.md#what-the-sdk-stores). Pick other names
for yours. Leave the alarm to the SDK, and use `schedule()` for your own
timed work, as `check_in()` does.

## Things to watch out for

**Call `super().__init__()` first.** It raises `RuntimeError` if the class
has no `containers` entry in the Wrangler config, and sets up the state,
schedules and alarm that every other method relies on. Set per-instance
attributes after it, as in [Setting values per
instance](container-class.md#setting-values-per-instance).

**Hooks can run more than once.** `on_start()` runs on every explicit start
call, even when the container was already running, and again on the first
request after `start()`. `on_stop()` runs again if it raises. Write both so a
repeat does no harm.

**`on_start()` blocks the object.** It runs inside
`blockConcurrencyWhile`, so no other call reaches the Durable Object until it
returns. Keep slow work out of it.

**Give every `Container` subclass its own class name.** `ContainerProxy`
finds a class's outbound handlers by class name, so two classes with the same
name in one Worker, even in different modules, share handlers. See
[Outbound rules and ContainerProxy](architecture.md#outbound-rules-and-containerproxy).

**Prefer the SDK's methods over `self.ctx.container`.** Starting or signalling
the container through the raw runtime object skips the SDK's state tracking,
monitor and outbound rules, so `get_state()` and the hooks stop matching what
the container is doing. Mixing raw calls with the SDK isn't a documented
contract, and how the two interact may change between releases.

**Using `crosswind-sandboxes` in the same class.** The sandboxes classes take
`self.ctx.container` and can run inside a `Container` subclass, but their
storage intercepts conflict with the SDK's outbound interception in some
configurations. See [the known
limitation](troubleshooting.md#sandboxes-intercepts-and-intercept-all).

## Next steps

- [Manage the lifecycle](lifecycle.md) for start options, timeouts and what
  a failed start leaves behind.
- [Container class](container-class.md) for every method used here.
- [How a container is managed](architecture.md) for the alarm and idle timer
  this guide relies on.
