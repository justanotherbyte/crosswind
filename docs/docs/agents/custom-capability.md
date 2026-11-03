---
title: "Build a capability for your agents"
description: "Write a LifecycleCapability with its own table, durable jobs, retries and an HTTP route, then install it on an Agent or a plain Durable Object."
---

A capability is how you add a feature that owns its own tables, jobs or routes, and reuse it across agents. It's the same thing `Agent` uses for schedules, the queue and state.

If you haven't seen capabilities before, [How an agent is built](architecture.md) explains why they exist, and when a plain method on your agent is the better choice.

We'll build an outbox. Your agent hands it a webhook to deliver, the outbox retries the delivery, and anything that keeps failing is stored so you can look at it later. It'll survive a crash, because the webhook is a job in SQLite from the moment you send it.

## Write the capability

```python
import json
from datetime import UTC, datetime
from urllib.parse import urlsplit

from workers import Request, Response, fetch

from agents import JobContext, JobOutcome, LifecycleCapability


class Outbox(LifecycleCapability):
    def __init__(self) -> None:
        super().__init__("outbox")

    async def on_start(self) -> None:
        self.lifecycle.sql(
            """CREATE TABLE IF NOT EXISTS outbox_failed (
                 id TEXT PRIMARY KEY, url TEXT NOT NULL, error TEXT NOT NULL
               )"""
        )

    async def send(self, url: str, body: str) -> str:
        job = await self.lifecycle.jobs.push(
            fn="deliver", time=datetime.now(UTC), payload={"url": url, "body": body}
        )
        return job.id

    async def on_job(self, context: JobContext) -> JobOutcome:
        payload = context.job.payload
        response = await fetch(payload["url"], method="POST", body=payload["body"])
        if not response.ok:
            raise RuntimeError(f"{payload['url']} answered {response.status}")
        return None

    async def on_job_error(self, context: JobContext, error: Exception) -> JobOutcome:
        self.lifecycle.sql(
            "INSERT OR REPLACE INTO outbox_failed VALUES (?, ?, ?)",
            context.job.id,
            context.job.payload["url"],
            str(error),
        )
        return None

    async def on_request(self, request: Request) -> Response | None:
        if not urlsplit(request.url).path.endswith("/outbox/failed"):
            return None
        rows = self.lifecycle.sql("SELECT * FROM outbox_failed")
        return Response(json.dumps(list(rows)))
```

Going through what each piece does:

- `super().__init__("outbox")` gives the capability its id. Its jobs are stored under this id, so keep it stable across deploys.
- `on_start` runs every time the object wakes, so the SQL has to be safe to run twice.
- `self.lifecycle` is everything the capability can use: `sql`, `jobs`, `storage`, `emit` and more. It raises `RuntimeError` until the capability is installed, so you can't call it from `__init__`.
- `on_job` runs when the job is due. Raising retries it, and returning `None` deletes it.
- `on_job_error` runs once the retries run out. By default that's 3 attempts. Without this hook the job is simply deleted, and the webhook is gone.
- `on_request` returns `None` for every request that isn't its own, and the request carries on to the next capability, then your agent.

## Install it

Call `self.use` in `__init__`. Startup locks the list, so `use` raises `RuntimeError` any later than that.

```python
from workers import Request, Response

from agents import Agent


class Orders(Agent):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        self.outbox = self.use(Outbox())

    async def on_request(self, request: Request) -> Response:
        await self.outbox.send("https://example.com/hooks", await request.text())
        return Response("queued")
```

`use` returns the capability, so you can keep it as an attribute. The Wrangler config and routing are the same as any other agent, as in [Getting started](getting-started.md).

Swap the webhook URL for one that answers with an error, then try it:

```sh
curl -X POST http://localhost:8787/agents/orders/demo -d 'order-1'
# queued

curl http://localhost:8787/agents/orders/demo/outbox/failed
# [{"id": "V1StGXR8_", "url": "https://your-url.example/hooks", "error": "https://your-url.example/hooks answered 503"}]
```

The delivery runs on the alarm, so the list stays empty until the retries finish. The second request never reaches `Orders.on_request`, because the outbox claims it first.

## Control what happens to a job

`on_job` and `on_job_error` return a `JobOutcome`:

| Return                  | What happens to the job                        |
| ----------------------- | ---------------------------------------------- |
| `None`                  | It's deleted.                                  |
| `Reschedule(at=when)`   | It runs again at `when`, a timezone-aware `datetime`. |
| `"yield"`               | It stays due, so the alarm runs it again almost at once. Returning this every time makes a hot loop. |

Pass `retry=RetryOptions(...)` to `push` to change the attempts and backoff. These retries all happen inside one alarm. For a delivery that should try again in an hour, return `Reschedule` from `on_job_error`.

`push` also takes an `id`. Pushing an id that already exists replaces that job, which is what you want for anything that repeats, like a sync. Without a stable id, each push adds another row. Once 10 or more of a capability's jobs are due in one alarm, the SDK logs a warning about it.

## Things to watch out for

**Jobs run at least once.** The job is only deleted after `on_job` returns, so a crash in between runs it again. Here, that means a webhook can be delivered twice. Send something the receiver can dedupe, like the job's id.

**Keep `on_job` short.** The alarm waits for each job in turn, so a slow job holds up every other job on the object, including your schedules. If the work has no time limit, start it as an `asyncio` task, keep a record of it in SQL, and return `Reschedule` so the job fires again soon. That matters, because a running task doesn't keep a Durable Object alive by itself, but the alarm does. [`Harness`](harness.md) works this way, with a heartbeat every 30 seconds. Pass the task to `self.lifecycle.track_alarm_work(task)` so the [memory limit breaker](lifecycle-class.md#memory-limits) still covers it.

**Hooks run outside the agent's context.** `get_current_agent()` returns `None` in a hook. If your capability calls code that relies on it, such as a callback the user gave you, wrap the call. `run_in_host_context` takes a function that returns an awaitable:

```python
await self.lifecycle.run_in_host_context(self.on_delivered)
```

**Don't push jobs to the agent itself.** `agent.lifecycle.jobs.push(...)` looks like it should work, but `Agent` has no handler for them, so the job is deleted with an error in the logs. Use `schedule()` or `queue()`, or your own capability.

**Pick an id that's free.** On an `Agent`, these are already taken and raise `RuntimeError`: `dynamic-agents`, `scheduler`, `queue`, `state`, `websockets`, `tasks`, `keep-alive` and `fibers`. `mcp` is taken if you turn MCP on, and an `AIChatAgent` also takes `sessions`, `streams` and `chat`.

**Name your tables.** Every capability shares the object's SQLite database. Prefix your tables with something unique, and leave `cf_agents_*` alone.

**Don't count on `dispose`.** It runs when a root agent is destroyed, not when the runtime evicts it from memory, and not when a sub-agent is deleted. Anything you need after a wake has to be in SQLite.

**Install it on a root agent if it uses jobs.** A sub-agent has no alarm of its own, and the routing the built-in capabilities use to get around that isn't a supported API yet. A capability with no jobs, such as one that only owns a table, works anywhere.

## Use it without an Agent

Capabilities don't depend on `Agent`. On a plain Durable Object, create the `Lifecycle` yourself and forward each entry point you need:

```python
from workers import DurableObject, Request, Response

from agents import Lifecycle


class Orders(DurableObject):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        self.outbox = Outbox()
        self.lifecycle = Lifecycle(self).use(self.outbox)

    async def fetch(self, request: Request) -> Response:
        return await self.lifecycle.fetch(request)

    async def alarm(self, alarm_info=None) -> None:
        await self.lifecycle.alarm()

    async def deliver(self, url: str, body: str) -> str:
        await self.lifecycle.start()
        return await self.outbox.send(url, body)
```

Without `alarm`, your jobs are never run. An RPC method like `deliver` skips `fetch`, so it has to call `self.lifecycle.start()` itself.

Address the object by name, with `getByName` or `idFromName`. Startup reads the name, so an object from `newUniqueId` fails to start and every request gets a `500`.

## Test it

The SDK's tests run capabilities under CPython against a fake runtime. [`test_capability_guide.py`](https://github.com/justanotherbyte/crosswind/blob/main/agents/tests/lifecycle/test_capability_guide.py) runs the outbox above and checks the rules on this page. The fake runtime lives in the repo and isn't part of the package, so copy it if you want to use it.

## Troubleshooting

### Why does `RuntimeError: ... must be installed with Lifecycle.use() before use` appear?

You used `self.lifecycle` before the capability was installed, usually in its `__init__`. Move that code to `on_start`.

### Why is my hook never called?

A hook only counts if a subclass overrides it, under its exact name. `on_requests` is just a new method, so nothing calls it and nothing warns you. Add `@typing.override` and a type checker will catch it.

### Why do I get `No installed capability or host handler for job ...` in the logs?

The job's owner is gone, or has no `on_job`. Either you pushed to `agent.lifecycle.jobs`, or you changed a capability's id or removed it while it still had jobs. The SDK drops these jobs.

### Why does `Lifecycle capabilities must be added before startup` appear?

Something called `use` after the object started. Move it to `__init__`.
