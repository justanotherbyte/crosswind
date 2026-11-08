---
title: "Harness class API reference"
description: "Python API reference for agents.Harness on Cloudflare: sessions, prompts, waiting, streaming events, abort, retry, fork, reset, transcripts and result types."
---

The full list of what a harness and its sessions can do. See [How a harness works](harness.md) for what these statuses mean, [Run a Pydantic AI agent](pydantic-ai.md) for a guide, and [Write your own harness](custom-harness.md) to implement `run_turn`. Every name here imports from `agents`, except the transcript records, which import from `agents.harness.transcript`.

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
| `reset_history(handoff)` | Override to choose the history a session starts from after `reset`. The default is empty. |

## `HarnessSession`

One conversation, with its history, transcript and operations.

| Method                                                       | Returns | Raises |
| ------------------------------------------------------------ | ------- | ------ |
| `async submit(input, *, operation_id=None, message_id=None, participant=None, when_busy="queue")` | [`Receipt`](#receipt), once the operation is stored and before the turn runs. | `SessionBusyError` if `when_busy="reject"` and the session has open work. `TypeError` if `input` mixes message parts and answers. `ValueError` if it's an empty list. |
| `async wait(operation_id)`                                   | [`OperationResult`](#operationresult), once it settles. | `OperationNotFoundError` |
| `async prompt(input, *, operation_id=None)`                  | `submit`, then `wait`. | |
| `stream(input, *, operation_id=None)`                        | An async iterator of this operation's [events](#events), until it settles. | |
| `watch()`                                                    | An async iterator of a [`SessionSnapshot`](#sessionsnapshot), then every event in the session. It doesn't end by itself. | |
| `async pending()`                                            | The open `PendingOperation`s, oldest first. | |
| `async abort(operation_id=None)`                             | Stops one open operation, or all of them. Queued work behind it runs. | |
| `async retry(operation_id)`                                  | Runs an `unanswered` operation again from its last checkpoint. | `OperationNotFoundError`. `ValueError` if it isn't `unanswered`, or a later operation has run. |
| `async reset(handoff=None)`                                  | Clears the history and the transcript, and publishes a `ResetEvent`. A `handoff` note starts the new transcript as a system message. | `SessionBusyError` if the session has open work. |

`input` is one of:

- A prompt `str`.
- A list of `TextInput(text=...)` and `FileInput(media_type=..., url=..., filename=None)` parts. `url` may be a `data:` URL.
- A list of `Approval` and `ToolOutput` answers. Answers to one turn's requests may arrive over several operations, and the first answer to each request wins.

`message_id` is the id of the user message the operation places in the transcript. It's the operation id if you leave it out.

`participant` is who sent the operation. A tool the client runs belongs to the participant who started the turn, and a `ToolOutput` from anyone else settles `unanswered` with the reason `"not-owner"`.

`operation_id` is how you make a retry from the client safe. Submitting an existing id stores nothing new and returns a `Receipt` with `accepted=False`. It's generated if you leave it out.

If you `stream` with an `operation_id` that has already settled, you get no events. Call `wait` to read its result.

## `HarnessSessions`

| Method             | Returns | Raises |
| ------------------ | ------- | ------ |
| `async create()`   | A new, empty `HarnessSession`. | |
| `async fork(id)`   | A new `HarnessSession` with a copy of session `id`'s history and transcript. | `SessionNotFoundError` |
| `async list()`     | A `SessionInfo` per session, oldest first. | |

`SessionInfo` has the session's `id`, the `parent` it was forked from or `None`, and `busy`, which is `True` while it has an operation queued or running.

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
| `reason`       | Why it's `unanswered`, such as `"interrupted"`, `"out_of_memory"`, `"not-owner"` or `"not-waiting"`. |

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

Each has a `type`.

| Event             | `type`          | Fields |
| ----------------- | --------------- | ------ |
| `SessionSnapshot` | `"snapshot"`    | `messages`, `pending`, `run`. Only `watch` yields this, as its first event. |
| `OperationEvent`  | `"operation"`   | `operation_id`, `status` |
| `MessageEvent`    | `"message"`     | `message`, a `TranscriptMessage` that replaces any with its id |
| `RunStartEvent`   | `"run-start"`   | `operations`, the ids the turn runs for. Published again when a turn resumes after a crash |
| `ChunkEvent`      | `"chunk"`       | `operation_id`, `chunk`, a `ResponseChunk` of the reply |
| `RunEndEvent`     | `"run-end"`     | `operations`. The reply has been saved |
| `ResetEvent`      | `"reset"`       | None. Messages after it belong to the new context |

`stream()` yields the `OperationEvent`, `RunStartEvent`, `ChunkEvent` and `RunEndEvent` events of its operation. `watch()` yields every event in the session. [The transcript](harness.md#the-transcript) lists the order a turn publishes them in.

Events live in memory and aren't stored. A client that reconnects calls `watch()` again and reads the transcript from its snapshot.

### `SessionSnapshot`

| Field      | Description |
| ---------- | ----------- |
| `messages` | The transcript since the last reset, as `TranscriptMessage`s. It leaves out what the run in progress has added. |
| `pending`  | The open `PendingOperation`s, oldest first. |
| `run`      | The run in progress as a `RunSnapshot`, or `None`. Its `operations` are the ids it runs for, and `partial` is the reply so far as chunks. The `ChunkEvent`s that follow continue it. |

To show the reply in progress, apply `run.partial` and each later chunk with `apply_chunks`. A run that continues after tool answers writes into the latest assistant message in `messages`. Any other run starts a new, empty assistant message. Its `MessageEvent` replaces what you built when the reply is saved.

## Transcript records

These import from `agents.harness.transcript`. They match upstream's Channels records field for field, and `to_wire` and `message_from_wire` convert them to and from upstream's camelCase JSON.

| Type                | Fields |
| ------------------- | ------ |
| `TranscriptMessage` | `id`, `role` (`"system"`, `"user"` or `"assistant"`), `parts`, `metadata=None` |
| `TextPart`, `ReasoningPart` | `text` |
| `FilePart`          | `media_type`, `url`, `filename=None` |
| `SourceUrlPart`, `SourceDocumentPart` | A cited page or document |
| `DataPart`          | `name`, `id=None`, `data`. A later part with the same `name` and `id` replaces it |
| `ToolPart`          | `tool_call_id`, `tool_name`, `state`, `input`, `output`, `error_text`, `approval`, `owner` |

A `ToolPart`'s `state` is `"input-streaming"`, `"input-available"`, `"approval-requested"`, `"approval-responded"`, `"output-available"`, `"output-error"` or `"output-denied"`. Its `owner` is the participant whose client runs it, or `None` for a tool the agent runs.

| Chunk | Fields |
| ----- | ------ |
| `TextStart`, `TextDelta`, `TextEnd` | `id`, and `delta` on the delta |
| `ReasoningStart`, `ReasoningDelta`, `ReasoningEnd` | `id`, and `delta` on the delta |
| `ToolInputStart`, `ToolInputDelta` | `tool_call_id`, and `tool_name` or `delta` |
| `ToolInputAvailable`, `ToolInputError` | `tool_call_id`, `tool_name`, `input`, and `error_text` on the error |
| `ToolApprovalRequest` | `tool_call_id`, `approval_id` |
| `ToolOutputAvailable`, `ToolOutputError`, `ToolOutputDenied` | `tool_call_id`, and `output` or `error_text` |
| `Data`, `Metadata`  | Application data, or metadata merged into the message |
| `FilePart`, `SourceUrlPart`, `SourceDocumentPart` | The part, sent whole |

`apply_chunks(message, chunks)` returns a copy of the message with the chunks applied, as upstream's `applyChunks` does. `ResponseGrammar().append(chunk)` checks that a run's chunks open and close each part in order, and raises `ResponseGrammarError` when they don't.

## Errors

These subclass `AgentsException`.

| Exception                | Raised when |
| ------------------------ | ----------- |
| `SessionBusyError`       | The session has open work and the call won't queue behind it. |
| `SessionNotFoundError`   | `fork` got an id with no session. |
| `OperationNotFoundError` | The session has no operation with this id. |
