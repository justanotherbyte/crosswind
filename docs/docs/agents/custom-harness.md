---
title: "Write your own harness"
description: "Subclass Harness to run your own agent loop or another framework in a Durable Object, with checkpoints, tools, approvals and crash recovery."
---

Subclass [`Harness`][agents.Harness] to run a framework other than Pydantic AI, or your own loop. The base class stores sessions and operations, queues prompts, publishes events and wakes the object after a crash. You implement one method, `run_turn`.

- `run_turn(turn)` runs one turn and returns a `TurnResult`.
- `turn.history` and `turn.checkpoint` are bytes in a format you choose.
- `turn.save(bytes)` writes a checkpoint. `turn.emit(event)` streams an event.
- `turn.tools` lists the `@tool` methods on `target`, already bound.
- Raising settles the operation `unanswered`, with the error message as its reason.

Read [How a harness runs turns](harness.md) first for the operation statuses and the recovery steps. The base harness needs only `cf-agents`, not the `pydantic-ai` extra.

## The smallest harness

This harness echoes each prompt and counts the turns in its history:

```python
from agents import Harness, TextEvent, Turn, TurnResult


class EchoHarness(Harness):
    async def run_turn(self, turn: Turn) -> TurnResult:
        count = int(turn.history or b"0") + 1
        text = f"{count}: {turn.input}"

        turn.emit(TextEvent(operation_id=turn.operation_id, text=text))
        return TurnResult(history=str(count).encode(), text=text)
```

Install it like the built-in harness, then prompt it twice from an `Agent`:

```python
from workers import Request, Response

from agents import Agent


class Echo(Agent):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        self.harness = self.use(EchoHarness("echo"))

    async def on_request(self, request: Request) -> Response:
        result = await self.harness.prompt(await request.text())
        return Response(result.text or "")
```

The first request answers `1: hi` and the second answers `2: hi`. The `TurnResult.history` you return becomes `turn.history` on the session's next turn.

Sessions, `stream()`, `abort()` and `operation_id` dedupe now work with no more code.

## What a turn receives

| `Turn` field   | What it holds                                                                 |
| -------------- | ----------------------------------------------------------------------------- |
| `session`      | The session id. `""` is the root session                                      |
| `operation_id` | The operation this turn answers. Put it on every event                        |
| `input`        | A prompt `str`, or a list of `Approval` and `ToolOutput` answers              |
| `history`      | The bytes the session's last turn returned. `None` for a new session          |
| `checkpoint`   | The last bytes passed to `save`. `None` unless the turn is being resumed or retried |
| `attempt`      | `0` on a first run, then how many times the turn was interrupted              |
| `tools`        | One `ToolSpec` per `@tool` method, with `fn`, `name`, `requires_approval` and `replay` |
| `emit`         | Publishes a `TextEvent`, `ToolCallEvent` or `ToolResultEvent`                 |
| `save`         | Replaces the checkpoint. It's synchronous, so don't `await` it                |

Return `TurnResult(history=..., text=...)` when the turn finishes. Return `TurnResult(history=..., status="needs_input", requests=[...])` when it stops on tool calls.

## A full agent loop

This harness runs tool calls against Workers AI with no framework. It saves a checkpoint before each step, asks for approval and handles an interrupted tool call on resume:

```python
import inspect
import json
from typing import Any, get_type_hints

from agents import (
    Approval,
    Harness,
    TextEvent,
    ToolAnswer,
    ToolCallEvent,
    ToolOutput,
    ToolRequest,
    ToolResultEvent,
    ToolSpec,
    Turn,
    TurnResult,
)
from agents.models import ChatMessage, Completion, ToolSchema, WorkersAIModel

INTERRUPTED = (
    "The agent restarted while this tool call was running, so it may or may "
    "not have taken effect. Check its effect before calling the tool again."
)
JSON_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean"}


class WorkersAIHarness(Harness):
    def __init__(self, id: str, model: WorkersAIModel, *, target: object) -> None:
        super().__init__(id, target=target)
        self.model = model
        self.specs = {spec.name: spec for spec in self.tools}
        self.schemas = [to_schema(spec) for spec in self.tools]

    async def run_turn(self, turn: Turn) -> TurnResult:
        messages: list[ChatMessage] = json.loads(turn.checkpoint or turn.history or "[]")
        answers = {} if isinstance(turn.input, str) else {a.id: a for a in turn.input}
        resuming = turn.checkpoint is not None

        if isinstance(turn.input, str) and not resuming:
            if unanswered(messages):
                raise ValueError("Answer the open tool requests first")
            messages.append({"role": "user", "content": turn.input})

        while True:
            turn.save(dump(messages))

            if calls := unanswered(messages):
                requests = await self.run_calls(turn, messages, calls, answers, resuming)
                if requests:
                    return TurnResult(
                        history=dump(messages), status="needs_input", requests=requests
                    )
            else:
                completion = await self.model.complete(messages, tools=self.schemas)
                messages.append(to_message(completion))
                if not completion.tool_calls:
                    turn.emit(
                        TextEvent(operation_id=turn.operation_id, text=completion.text)
                    )
                    return TurnResult(history=dump(messages), text=completion.text)

            resuming = False

    async def run_calls(
        self,
        turn: Turn,
        messages: list[ChatMessage],
        calls: list[Any],
        answers: dict[str, ToolAnswer],
        resuming: bool,
    ) -> list[ToolRequest]:
        requests = []
        for call in calls:
            id, name = call["id"], call["function"]["name"]
            args = json.loads(call["function"]["arguments"])
            spec = self.specs[name]

            match answers.get(id):
                case ToolOutput(value=value):
                    result = value
                case Approval(approved=False, message=message):
                    result = message or "The user denied this call."
                case None if spec.requires_approval:
                    requests.append(
                        ToolRequest(id=id, name=name, args=args, kind="approval")
                    )
                    continue
                case _ if resuming and spec.replay == "unsafe":
                    result = INTERRUPTED
                case _:
                    result = await self.run_tool(turn, spec, id, args)

            messages.append(
                {"role": "tool", "tool_call_id": id, "content": json.dumps(result)}
            )
            turn.save(dump(messages))
        return requests

    async def run_tool(
        self, turn: Turn, spec: ToolSpec, id: str, args: dict[str, Any]
    ) -> Any:
        operation_id = turn.operation_id
        turn.emit(
            ToolCallEvent(
                operation_id=operation_id, tool_call_id=id, name=spec.name, args=args
            )
        )

        result = await spec.fn(**args)
        turn.emit(
            ToolResultEvent(operation_id=operation_id, tool_call_id=id, result=result)
        )
        return result


def dump(messages: list[ChatMessage]) -> bytes:
    return json.dumps(messages).encode()


def unanswered(messages: list[ChatMessage]) -> list[Any]:
    answered = set()
    for message in reversed(messages):
        if message["role"] == "assistant":
            calls = message.get("tool_calls", [])
            return [call for call in calls if call["id"] not in answered]
        if message["role"] != "tool":
            return []
        answered.add(message["tool_call_id"])
    return []


def to_message(completion: Completion) -> ChatMessage:
    message: ChatMessage = {"role": "assistant", "content": completion.text}
    if completion.tool_calls:
        message["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments),
                },
            }
            for call in completion.tool_calls
        ]
    return message


def to_schema(spec: ToolSpec) -> ToolSchema:
    hints = get_type_hints(spec.fn)
    names = list(inspect.signature(spec.fn).parameters)
    # shortcut: only str, int, float and bool arguments; use a schema library for more.
    properties = {name: {"type": JSON_TYPES[hints[name]]} for name in names}
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": inspect.getdoc(spec.fn) or "",
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": names,
            },
        },
    }
```

Host it on an agent. Tools are `async` methods marked with `@tool`:

```python
from workers import Request, Response

from agents import Agent, Approval, tool
from agents.models import WorkersAIModel


class Notes(Agent):
    initial_state = {"notes": []}

    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        model = WorkersAIModel("@cf/qwen/qwen3-30b-a3b-fp8", binding=env.AI)
        self.harness = self.use(WorkersAIHarness("notes", model, target=self))

    @tool
    async def add_note(self, text: str) -> str:
        """Save a note."""
        self.set_state({"notes": [*self.state["notes"], text]})
        return "Saved"

    @tool(requires_approval=True)
    async def clear_notes(self) -> str:
        """Delete every note."""
        self.set_state({"notes": []})
        return "Cleared"

    async def on_request(self, request: Request) -> Response:
        result = await self.harness.prompt(await request.text())

        if result.status == "needs_input":
            answers = [Approval(id=call.id, approved=True) for call in result.requests]
            result = await self.harness.prompt(answers)

        return Response(result.text or result.reason or "")
```

A prompt of `Remember to buy milk` makes the model call `add_note`, and the agent replies with the model's answer. This handler approves every request to keep the sample short. A real client shows the request to the user first.

- The history is the Workers AI message list as JSON. Any format works, because the base class stores opaque bytes.
- The checkpoint uses the same format as the history, so `run_turn` loads both with one line.
- `unanswered` finds the next step from the messages alone, so a first run, a resume and a retry all use the same loop.
- To stream text, replace `complete` with [`model.stream`][agents.models.WorkersAIModel.stream], emit a `TextEvent` per `TextDelta`, and join the `ToolCallDelta` pieces into calls.
- A tool the client runs has `kind="call"` on its `ToolRequest`. The `ToolOutput` case above takes its answer. To request one, add its schema to `self.schemas` and return a `kind="call"` request when `self.specs` doesn't have the tool.

## Rules for a durable turn

Follow these or a crash loses or repeats work:

- **Save before each step that costs money or has side effects.** Work after the last `save` runs again on resume. Without any `save`, a resumed turn starts over from `turn.history` and `turn.input`.
- **Check `turn.checkpoint` before adding the prompt.** The checkpoint already holds it. This sample guards with `not resuming`, and without the guard a resumed turn sends the prompt to the model twice.
- **Honor `spec.replay`.** On resume, a tool with `replay="unsafe"` may have already run. Report it to the model as interrupted. Rerun only `replay="safe"` calls, and only if they need no approval or the client approved them.
- **Never approve on the client's behalf.** Keep the client's `Approval` and `ToolOutput` answers on resume. They come back in `turn.input`.
- **Let exceptions propagate.** The base class tells platform failures from your own errors. Catching `Exception` turns a deploy that should resume into a failed turn or a false success, and catching `asyncio.CancelledError` breaks `abort()`.
- **Keep checkpoints under 2 MB.** That's the [Durable Objects limit](https://developers.cloudflare.com/durable-objects/platform/limits/) for one SQLite value. Over it, `save` raises and the turn settles `unanswered`.
- **Keep the harness `id` stable.** It's part of every row's key. A new `id` starts with empty sessions and leaves the old rows behind.

## Set the recovery options

Pass `max_restarts` and `heartbeat` to `super().__init__`:

```python
from datetime import timedelta

from agents import Harness


class PatientHarness(Harness):
    def __init__(self, id: str, *, target: object | None = None) -> None:
        super().__init__(id, target=target, max_restarts=2, heartbeat=timedelta(minutes=1))
```

`heartbeat` is a number of seconds or a `timedelta`. `max_restarts` below 0 or a `heartbeat` of 0 or less raises `ValueError`.

`Harness` implements the Lifecycle hooks `on_start`, `on_job`, `on_memory_limit` and `dispose`. If you override one, call `await super().on_start()` (or the matching hook) first. Without that call, the harness never creates its tables or resumes turns.

## Test a harness

Run the turn logic against a fake binding. To test a crash, keep the storage, build a new host and harness on it, and call `lifecycle.start()` and `lifecycle.alarm()`. [`test_harness.py`](https://github.com/justanotherbyte/crosswind/blob/main/agents/tests/harness/test_harness.py) does this in `test_wake_job_resumes_a_turn_from_its_checkpoint_after_a_crash`, using the repo's `fake_runtime`.

For a framework adapter to copy, read [`PydanticAIHarness`](https://github.com/justanotherbyte/crosswind/blob/main/agents/src/agents/harness/pydantic_ai/harness.py), which is 154 lines.

## Troubleshooting

### Why does my turn start over after a crash?

`turn.checkpoint` is `None` until the turn calls `turn.save`. Call it before the first step.

### Why did the model receive my prompt twice?

`run_turn` added `turn.input` to a checkpoint that already held it. Add the prompt only when `turn.checkpoint` is `None`.

### Why does `RuntimeError: ... must be installed with Lifecycle.use() before use` appear?

The harness isn't installed. Call `self.use(harness)` on an `Agent`, or `Lifecycle(self).use(harness)` on a plain Durable Object, in `__init__`.
