---
title: "Lifecycle and capabilities API reference"
description: "Python API reference for LifecycleCapability on Cloudflare Durable Objects: hooks, dispatch rules, LifecycleServices, the job queue, job outcomes and Lifecycle."
---

The full list of hooks, services and options for writing a capability. See [How an agent is built](architecture.md) for why these exist, and [Build a capability](custom-capability.md) for a guide. Every name here imports from `agents`.

`on_route`, `self.lifecycle.routes` and the `_set_*` methods on `Lifecycle` also exist. The SDK uses them for its own sub-agent routing, and they aren't a supported API yet.

## `LifecycleCapability`

```python
class LifecycleCapability:
    claims: ClassVar[Literal["selective", "catch-all"]] = "selective"

    def __init__(self, capability_id: str) -> None: ...

    @property
    def lifecycle(self) -> LifecycleServices: ...
```

- `capability_id` owns the capability's jobs and names its events. A blank id raises `ValueError`, and an id already installed on the `Lifecycle` raises `RuntimeError` from `use`.
- `claims = "catch-all"` always dispatches last. A second catch-all on `on_request` or `on_websocket_upgrade` raises `RuntimeError` from `use`.
- `lifecycle` raises `RuntimeError` until the capability is installed.

### Hooks

Every hook is `async` and optional. A hook counts as implemented only if a subclass overrides it.

| Hook                                                | Called                                     | Return |
| --------------------------------------------------- | ------------------------------------------ | ------ |
| `on_start()`                                        | Once per wake, before any other event. Runs on every capability in install order, then the host's `on_start`. | Nothing. Raising fails startup, and the next event tries again. |
| `on_request(request)`                               | For an HTTP request, until one claims it.  | A `Response` to claim it, or `None`. |
| `on_websocket_upgrade(request)`                     | For a WebSocket upgrade, until one claims it. | A `Response` to claim it and own the socket, or `None`. |
| `on_websocket_message(ws, message)`                 | For a message, until one consumes it. `message` is `str` or `bytes`. | `True` to consume it. |
| `on_websocket_close(ws, code, reason, was_clean)`   | For a close, until one consumes it.        | `True` to consume it. |
| `on_websocket_error(ws, error)`                     | For an error, until one consumes it. Teardown errors on a closing socket never arrive. | `True` to consume it. |
| `on_job(context)`                                   | When a job this capability pushed is due.  | A [`JobOutcome`](#joboutcome). |
| `on_job_error(context, error)`                      | When that job's retries run out. A platform failure doesn't count, since the job is kept for the next alarm. | A [`JobOutcome`](#joboutcome). Raising completes the job. Without this hook, the job completes. |
| `on_memory_limit(context)`                          | After an alarm [memory limit strike](#memory-limits). Runs on every capability that implements it. | Nothing. Raising is logged. |
| `dispose()`                                         | When a root `Agent` is destroyed, or a plain Durable Object calls `lifecycle.dispose()`, in reverse install order. Not on eviction, and not when a sub-agent is deleted. | Nothing. Raising is logged. |

An exception from `on_request` or `on_websocket_upgrade` gives the client a `500` or an error frame, and the traceback goes to the logs. An exception from a message, close or error hook is logged.

Hooks run outside the host context, so call user code through [`run_in_host_context`](#lifecycleservices).

## `LifecycleServices`

What `self.lifecycle` gives a capability.

| Member                                                     | Description |
| ---------------------------------------------------------- | ----------- |
| `name`                                                     | The Durable Object's name. |
| `class_name`                                               | The host class's name. |
| `sql`                                                      | [`Sql`](agent-class.md#sql) over the object's database. Capabilities share it, so name your tables carefully. |
| `storage`                                                  | The host's `ctx.storage`. |
| `jobs`                                                     | This capability's [`LifecycleJobs`](#lifecyclejobs). |
| `accept_websocket(ws, tags)`                               | Accepts `ws` into hibernation under `tags`. |
| `websockets(tag=None)`                                     | Every hibernated socket on the object, including other capabilities', optionally by tag. |
| `async ready()`                                            | Waits for startup, starting it if needed. Returns at once inside startup. |
| `status()`                                                 | `"zero"`, `"starting"` or `"started"`. |
| `track_alarm_work(work)`                                   | Keeps a task that a job started under the [memory limit breaker](#memory-limits). Returns `False` and tracks nothing outside an alarm. |
| `async run_in_host_context(fn, *, connection=None, request=None)` | Awaits `fn()` in the host's context. On an `Agent`, that sets `get_current_agent()`. On a plain Durable Object, it still returns `None`. |
| `emit(type, payload)`                                      | Publishes a best-effort event. An `Agent` forwards `type` and `payload`, without the capability's id, to its [`observability`](agent-class.md#class-attributes), so make `type` distinctive. A failing sink is logged and never fails your work. |

## `LifecycleJobs`

```python
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
) -> LifecycleJob
```

| Parameter       | Default | Description |
| --------------- | ------- | ----------- |
| `fn`            | Required | The name your `on_job` dispatches on. A blank name raises `ValueError`. |
| `time`          | Required | When the job is due, as a timezone-aware `datetime`. A time before 1970 raises `ValueError`. |
| `payload`       | `None`  | JSON data, stored with the job. |
| `id`            | Generated | A stable id. Pushing an existing id replaces that job, and wins over any outcome a running dispatch returns. An id owned by another capability raises `ValueError`. |
| `retry`         | 3 attempts, 0.1 s base, 3 s max | [`RetryOptions`](agent-class.md#retryoptions) for dispatches within one alarm. |
| `singleflight`  | `False` | Skip this job while a previous run of it is still in flight. |
| `hung_timeout`  | 30 s    | When an in-flight `singleflight` run counts as hung and gets reset, and when any dispatch counts as slow. |
| `exclusive`     | `False` | While this job is pending, the alarm is set for it and no other job. |
| `recovery_loop` | `False` | Put this job under the [memory limit breaker](#memory-limits). |

| Method                         | Description |
| ------------------------------ | ----------- |
| `async cancel(id)`             | Cancels one job. Returns whether it existed. |
| `async reschedule(id, time)`   | Moves one job to `time`. Returns whether it existed. |
| `get(id)`                      | One `LifecycleJob`, or `None`. |
| `list()`                       | Every job this capability holds, earliest first. |
| `async rearm()`                | Recalculates the alarm without changing any job. Other changes do this for you. |

### `LifecycleJob`

A dataclass with `id`, `capability`, `fn`, `time`, `payload`, `retry`, `singleflight`, `exclusive`, `recovery_loop` and `created_at`. `capability` is the owner's id, or `"host"`.

### `JobContext`

A dataclass with `job`, the `LifecycleJob`, and `attempt`, the attempt number within this alarm.

### `JobOutcome`

```python
type JobOutcome = Reschedule | Literal["yield"] | None
```

| Value                  | Effect |
| ---------------------- | ------ |
| `None`                 | Deletes the job. |
| `Reschedule(at=when)`  | Runs it again at `when`. |
| `"yield"`              | Leaves it due, so the alarm runs it again almost at once. |

A push or reschedule of the same id during the dispatch wins over the returned outcome.

### Failures

| What happened                          | Result |
| -------------------------------------- | ------ |
| `on_job` raised, attempts remain       | Retries with backoff, inside this alarm. |
| `on_job` raised, attempts ran out      | `on_job_error`, or the job completes. |
| A platform failure, such as a deploy   | The job is kept. The error goes back to the platform's alarm retry. |
| The job's owner is gone, or has no `on_job` | The job is deleted, with an error in the logs. |

Before running due jobs, the SDK sets the alarm 30 seconds ahead, so an isolate that dies mid-run still wakes up to finish the queue.

## Memory limits

An alarm that hits the Durable Object memory limit would otherwise get retried by the platform, hit the limit again and loop. `Lifecycle` counts these as strikes, and clears the count after a clean alarm.

| Strike | What happens |
| ------ | ------------ |
| Below the limit | The job that was running, and every `recovery_loop` job due before then, move to `30 s x strikes` from now, capped at 300 s. |
| At the limit, 3 by default | The job that was running and every `recovery_loop` job are deleted, and the count resets. |

Each strike calls `on_memory_limit` on every capability, then resets the isolate. Set the limit with [`AgentOptions.max_alarm_memory_limit_strikes`](agent-class.md#agentoptions), or `max_alarm_memory_limit_strikes` on `Lifecycle`.

### `MemoryLimitContext`

| Field                       | Description |
| --------------------------- | ----------- |
| `sealed`                    | Whether the limit was reached and `recovery_loop` jobs were deleted. |
| `next_time`                 | When the backoff wakes the object, for a strike below the limit. |
| `executing`                 | The `LifecycleJob` that was running, if any. |
| `purged_recovery_loop_jobs` | The jobs that were deleted, on the strike that deleted them. |

## `Lifecycle`

```python
class Lifecycle:
    def __init__(
        self,
        host: Any,
        *,
        hooks: object | None = None,
        max_alarm_memory_limit_strikes: int = 3,
        expose_error_details: bool = False,
    ) -> None: ...
```

An `Agent` creates its own, at `self.lifecycle`. You only create one on a plain Durable Object.

- `host` is the Durable Object.
- `hooks` is the object whose `on_start`, `on_request`, `on_alarm` and `on_job` get called, if it has them. It defaults to `host`.
- `expose_error_details` sends tracebacks to clients. Keep it off outside local development, since a traceback can reveal code, paths and data.

| Member                                  | Description |
| --------------------------------------- | ----------- |
| `use(capability)`                       | Installs a capability and returns this `Lifecycle`. On an `Agent`, call `self.use`, which returns the capability instead. |
| `async start()`                         | Starts the capabilities and host, once per wake. RPC methods call this themselves, since RPC skips `fetch`. |
| `is_started()`                          | Whether startup has finished. |
| `async fetch(request)`                  | Handles an HTTP request or WebSocket upgrade. |
| `async alarm()`                         | Runs due jobs, then `on_alarm`, then sets the next alarm. |
| `async websocket_message(ws, message)`, `websocket_close(ws, code, reason, was_clean)`, `websocket_error(ws, error)` | Dispatch a hibernated socket's events. |
| `jobs`                                  | The host's own jobs, which go to `on_job` on `hooks`. On an `Agent`, nothing handles these. |
| `sql`, `name`, `class_name`             | As on [`LifecycleServices`](#lifecycleservices). `name` raises `RuntimeError` if the object wasn't addressed by name, and startup reads it, so such an object can't start. |
| `async rearm_alarm()`                   | Recalculates the alarm. |
| `async dispose()`                       | Disposes capabilities in reverse order. |
| `async disable_alarms()`                | Permanently stops alarms and deletes the alarm, for host teardown. |

### `LifecycleEvent`

A dataclass with `source`, `type` and `payload`, as published by `emit`. Without a sink, `Lifecycle` writes each event as JSON to the `agents.events` logger at `DEBUG`.
