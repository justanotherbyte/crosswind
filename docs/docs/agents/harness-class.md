---
title: "Harness class API reference"
description: "Reference for agents.Harness and its sessions: submitting prompts, waiting, streaming and watching events, aborting, retrying, forking, and every result and event type."
---

The full list of what a harness and its sessions can do. See [How a harness works](harness.md) for what these statuses mean, [Run a Pydantic AI agent](pydantic-ai.md) for a guide, and [Write your own harness](custom-harness.md) to implement `run_turn`. Every name here imports from `agents`.

## `Harness`

```python
class Harness(LifecycleCapability):
    def __init__(
        self,
        id: str,
        *,
        target: object | None = None,
        max_restarts: int = 5,
        heartbeat: Duration = 30,
    ) -> None: ...
```

| Parameter      | Default | Description |
| -------------- | ------- | ----------- |
| `id`           | Required | The capability id. It keys this harness's rows, so keep it stable, and give two harnesses on one object different ids. |
| `target`       | `None`  | The object whose `@tool` methods become tools, usually `self`. |
| `max_restarts` | `5`     | How many times a turn is resumed after a crash before it settles `unanswered`. A negative value raises `ValueError`. |
| `heartbeat`    | `30`    | How often a session with open work is checked, and the longest a turn waits to resume with no request to wake the object. Zero or less raises `ValueError`. |

| Member                  | Description |
| ----------------------- | ----------- |
| `session(id="")`        | A [`HarnessSession`](#harnesssession). `""` is the root session. No I/O happens until you call something on it. |
| `sessions`              | [`HarnessSessions`](#harnesssessions), to create, fork and list. |
| `async prompt(input)`   | `session().prompt(input)`. |
| `tools`                 | A `ToolSpec` per `@tool` method on `target`. |
| `target`                | The object the tools came from. |
| `async run_turn(turn)`  | Implement this in a subclass. See [Write your own harness](custom-harness.md). |

## `HarnessSession`

One conversation, with its history and operations.

| Method                                                       | Returns | Raises |
| ------------------------------------------------------------ | ------- | ------ |
| `async submit(input, *, operation_id=None, when_busy="queue")` | [`Receipt`](#receipt), once the operation is stored and before the turn runs. | `SessionBusyError` if `when_busy="reject"` and the session has open work. |
| `async wait(operation_id)`                                   | [`OperationResult`](#operationresult), once it settles. | `OperationNotFoundError` |
| `async prompt(input, *, operation_id=None)`                  | `submit`, then `wait`. | |
| `stream(input, *, operation_id=None)`                        | An async iterator of this operation's [events](#events), until it settles. | |
| `watch()`                                                    | An async iterator of a `SessionSnapshot`, then every event in the session. It doesn't end by itself. | |
| `async pending()`                                            | The open `PendingOperation`s, oldest first. | |
| `async abort(operation_id=None)`                             | Stops one open operation, or all of them. Queued work behind it runs. | |
| `async retry(operation_id)`                                  | Runs an `unanswered` operation again from its last checkpoint. | `OperationNotFoundError`. `ValueError` if it isn't `unanswered`, or a later operation has run. |
| `async reset()`                                              | Clears the history. | `SessionBusyError` if the session has open work. |

`input` is a prompt `str`, or a list of `Approval` and `ToolOutput` answers.

`operation_id` is how you make a retry from the client safe. Submitting an existing id stores nothing new and returns a `Receipt` with `accepted=False`. It's generated if you leave it out.

If you `stream` with an `operation_id` that has already settled, you get no events. Call `wait` to read its result.

## `HarnessSessions`

| Method             | Returns | Raises |
| ------------------ | ------- | ------ |
| `async create()`   | A new, empty `HarnessSession`. | |
| `async fork(id)`   | A new `HarnessSession` with a copy of session `id`'s history. | `SessionNotFoundError` |
| `async list()`     | Every session id, oldest first. | |

## Results

### `Receipt`

| Field          | Description |
| -------------- | ----------- |
| `session`      | The session id. |
| `operation_id` | The operation's id. |
| `accepted`     | `False` if an operation with this id already existed. |

### `OperationResult`

| Field          | Description |
| -------------- | ----------- |
| `session`, `operation_id` | As on `Receipt`. |
| `status`       | One of the settled [statuses](harness.md#how-an-operation-moves): `"done"`, `"needs_input"`, `"unanswered"` or `"aborted"`. |
| `text`         | The answer, when `done`. |
| `requests`     | The `ToolRequest`s to answer, when `needs_input`. |
| `reason`       | Why it's `unanswered`, such as `"interrupted"` or `"out_of_memory"`. |

### `PendingOperation`

`session`, `operation_id`, and a `status` of `"queued"` or `"running"`.

## Tool calls

| Type          | Fields | Description |
| ------------- | ------ | ----------- |
| `ToolRequest` | `id`, `name`, `args`, `kind` | A call the turn stopped on. `kind="approval"` wants an `Approval`. `kind="call"` wants the client to run it and send a `ToolOutput`. |
| `Approval`    | `id`, `approved`, `message=None` | The client's decision. |
| `ToolOutput`  | `id`, `value` | The result of a tool the client ran. |
| `ToolSpec`    | `fn`, `name`, `requires_approval`, `replay` | A bound `@tool` method. |

### `@tool`

```python
@tool
@tool(requires_approval=False, replay="unsafe")
```

Marks an `async` method as a tool. Its name, signature and docstring describe it to the model. A non-`async` function raises `TypeError`, and a `replay` other than `"safe"` or `"unsafe"` raises `ValueError`. See [What can repeat](harness.md#what-can-repeat) for what `replay` means.

## Events

Each has a `type`. Every event except the snapshot has an `operation_id`.

| Event             | `type`          | Other fields |
| ----------------- | --------------- | ------------ |
| `SessionSnapshot` | `"snapshot"`    | `operations`. Only `watch` yields this, as its first event. |
| `OperationEvent`  | `"operation"`   | `status` |
| `TextEvent`       | `"text"`        | `text` |
| `ToolCallEvent`   | `"tool-call"`   | `tool_call_id`, `name`, `args` |
| `ToolResultEvent` | `"tool-result"` | `tool_call_id`, `result`, `is_error=False` |

Events live in memory and aren't stored, so a client that reconnects misses what was sent while it was away. Use `wait` for the result.

## Errors

These subclass `AgentsException`.

| Exception                | Raised when |
| ------------------------ | ----------- |
| `SessionBusyError`       | The session has open work and the call won't queue behind it. |
| `SessionNotFoundError`   | `fork` got an id with no session. |
| `OperationNotFoundError` | The session has no operation with this id. |
