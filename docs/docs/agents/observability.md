---
title: "Observe what a Python agent is doing"
description: "Observe a Python agent on Cloudflare: turn on the structured events it emits, send them to your own sink, subscribe to them in tests and look up each payload."
---

Every agent emits structured events as it works: RPC calls, state changes, retries, failed jobs and more. They're off in your logs by default, because the default sink writes them at `DEBUG`.

!!! note
    The events here use the same names and payloads as the TypeScript SDK, but not every event there is ported yet. Tracing isn't ported either.

## See the events

Turn up the `agents.events` logger:

```python
import logging

logging.getLogger("agents.events").setLevel(logging.DEBUG)
```

Each event is then one line of JSON. Calling `increment` on a counter logs something like:

```json
{"type":"rpc","agent":"Counter","name":"my-counter","payload":{"method":"increment"},"timestamp":1793664000000}
```

On Workers, the SDK sends its logs to the JavaScript console for you. Don't add `logging.basicConfig()` as well, or each line appears twice.

## Send events somewhere else

Set `observability` to any object with an `emit(event)` method. This one prints events whose name ends in `:error`:

```python
from agents import Agent, ObservabilityEvent


class ErrorsOnly:
    def emit(self, event: ObservabilityEvent) -> None:
        if event.type.endswith(":error"):
            print(event.type, event.agent, event.name, event.payload)


class Counter(Agent):
    observability = ErrorsOnly()
```

`print` output goes to [Workers Logs](https://developers.cloudflare.com/workers/observability/logs/workers-logs/). This doesn't catch every failure, as `task:failed`, `stream:errored`, `fiber:run:failed` and `alarm:memory_limit_reset` have other names, so check the table below for what you need.

If `emit` needs a binding, set it in `__init__`, where you have `env`:

```python
class Counter(Agent):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        self.observability = Analytics(env.EVENTS)
```

`Analytics` stands for your own class, which could call `write_data_point` on an [Analytics Engine binding](../workers/bindings.md). Keep `emit` quick, because it's synchronous and runs in the middle of the work it's reporting. If it raises, the SDK logs that and carries on, so a broken sink can't fail your agent.

An `ObservabilityEvent` has `type`, `agent`, `name`, `payload` and `timestamp`, a `datetime` in UTC. Set `observability = None` to turn events off.

Setting your own sink replaces the default, so you won't get the log lines or [`subscribe`](#watch-events-in-a-test) unless you call `LoggingObservability().emit(event)` yourself.

## Watch events in a test

`subscribe(channel, listener)` calls `listener` for each event the default sink sees on a channel. `listener` has to be a plain function, since an `async` one raises `TypeError`. It works in-process only, so it's for tests and local debugging, not for production:

```python
from agents import subscribe

seen = []
handle = subscribe("state", lambda event: seen.append(event.type))
agent.set_state({"count": 1})
handle.dispose()

print(seen)  # ['state:update']
```

Each event goes to one channel, shown in the table below.

## Emit your own

A [capability](custom-capability.md) can publish its own with `self.lifecycle.emit("outbox:failed", {"id": job_id})`. The sink gets the `type` and `payload`, so make the name distinctive. `channel` is worked out from the prefix, and an unknown prefix goes to `lifecycle`.

## Every event

Payload keys are camelCase, as in the TypeScript SDK. A key in brackets is only sometimes there. The `step` in the `task:attempt:interrupted` payload and the `reason` in `task:cancelled` can be `null`.

| Channel | Event | Payload |
| ------- | ----- | ------- |
| `lifecycle` | `connect` | `connectionId` |
| `lifecycle` | `disconnect` | `connectionId`, `code`, `reason` |
| `lifecycle` | `destroy` | None |
| `lifecycle` | `job:slow_dispatch` | `capability`, `fn`, `id`, `thresholdMs` |
| `lifecycle` | `job:backlog_warning` | `capability`, `count` |
| `lifecycle` | `alarm:memory_limit_reset` | `strikes`, `limit`, `sealed`, `error` |
| `rpc` | `rpc` | `method`, (`streaming`) |
| `rpc` | `rpc:error` | `method`, `error` |
| `state` | `state:update` | None |
| `schedule` | `schedule:create`, `schedule:execute`, `schedule:cancel` | `callback`, `id` |
| `schedule` | `schedule:retry` | `callback`, `id`, `attempt`, `maxAttempts` |
| `schedule` | `schedule:error` | `callback`, `id`, `error`, `attempts` |
| `schedule` | `queue:create` | `callback`, `id` |
| `schedule` | `queue:retry` | `callback`, `id`, `attempt`, `maxAttempts` |
| `schedule` | `queue:error` | `callback`, `id`, `error`, `attempts` |
| `task` | `task:accepted` | `definition`, `runId`, `accepted` |
| `task` | `task:attempt:started` | `definition`, `runId`, `attempt` |
| `task` | `task:attempt:interrupted` | `definition`, `runId`, `attempt`, `step` |
| `task` | `task:step:started`, `task:step:completed`, `task:step:retry` | `definition`, `runId`, `step`, `attempt` |
| `task` | `task:waiting` | `definition`, `runId`, `reason`, `wakeAt` |
| `task` | `task:completed`, `task:deleted` | `definition`, `runId` |
| `task` | `task:failed` | `definition`, `runId`, `error` |
| `task` | `task:cancelled` | `definition`, `runId`, `reason` |
| `fiber` | `fiber:run:started` | `fiberId`, `fiberName`, `managed` |
| `fiber` | `fiber:run:completed` | `fiberId`, `fiberName`, `managed`, `elapsedMs` |
| `fiber` | `fiber:run:failed` | `fiberId`, `fiberName`, `managed`, `elapsedMs`, `error` |
| `fiber` | `fiber:run:interrupted` | `fiberId`, `fiberName`, `managed`, `elapsedMs`, `recoveryReason` |
| `fiber` | `fiber:recovery:attempt` | `fiberId`, `fiberName`, `managed`, `recoveryReason` |
| `fiber` | `fiber:recovery:detected` | `fiberId`, `fiberName`, `managed`, `recoveryReason`, `elapsedMs` |
| `fiber` | `fiber:recovery:handled` | `fiberId`, `fiberName`, `managed`, `recoveryReason`, `elapsedMs`, `status` |
| `fiber` | `fiber:recovery:failed` | `fiberId`, `fiberName`, `managed`, `recoveryReason`, `elapsedMs`, `error`, `reason` |
| `fiber` | `fiber:recovery:skipped` | `fiberId`, `fiberName`, `reason`, `elapsedMs`, (`managed`) |
| `stream` | `stream:opened`, `stream:deleted` | `streamId` |
| `stream` | `stream:closed` | `streamId` |
| `stream` | `stream:errored` | `streamId`, (`reason`) |
| `lifecycle` | `session:message:appended` | `sessionId`, `messageId`, `tokenEstimate` |
| `lifecycle` | `session:message:updated` | `sessionId`, `messageId` |
| `lifecycle` | `session:messages:deleted` | `sessionId`, `count` |
| `lifecycle` | `session:cleared` | `sessionId` |
| `lifecycle` | `session:error` | `sessionId`, `event`, `error` |
| `message` | `message:request`, `message:response`, `message:clear` | None |
| `message` | `message:cancel` | `requestId` |
| `message` | `message:error` | `error` |
| `mcp` | `mcp:client:preconnect` | `serverId` |
| `mcp` | `mcp:client:authorize` | `serverId`, `authUrl`, (`clientId`) |
| `mcp` | `mcp:client:connect` | `url`, `transport`, `state`, (`error`) |
| `mcp` | `mcp:client:discover` | `url`, (`capability`), (`state`), (`error`) |
| `mcp` | `mcp:client:close` | `url`, `transport`, `state`, (`phase`), (`error`) |
