---
title: "Run a Pydantic AI agent in a Durable Object"
description: "Install the Pydantic AI harness, give the model tools from your Agent class, approve tool calls, stream responses and resume after a crash."
---

`PydanticAIHarness` runs a [Pydantic AI](https://ai.pydantic.dev/) agent inside your Durable Object. It stores the conversation in SQLite, queues prompts and resumes a turn after a crash.

## Installation

!!! note
    Python 3.13 or 3.14 Workers are required. The harness doesn't run on 3.12.

Run the following in your terminal:
```sh
uv add "cf-agents[pydantic-ai]"
```

For Python 3.14 Workers, use uv 0.13.4 or later and add the `python_workers_314` compatibility flag.

For Python 3.13 Workers with pywrangler, add this to your `pyproject.toml`. pywrangler's default index stops at pydantic 2.10, which is too old for Pydantic AI 2.64.
```toml
[tool.uv.pip]
extra-index-url = ["https://index.pyodide.org/0.29.3"]
```

It must go under `[tool.uv.pip]`. Under `[tool.uv]`, it breaks `uv sync`.

## Quickstart

This agent saves notes when the model asks it to:

```python
import pydantic_ai
from workers import Request, Response, WorkerEntrypoint

from agents import Agent, route_agent_request, tool
from agents.harness.pydantic_ai import PydanticAIHarness, WorkersAIModel


class Notes(Agent):
    initial_state = {"notes": []}

    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        model = WorkersAIModel("@cf/qwen/qwen3-30b-a3b-fp8", binding=env.AI)
        agent = pydantic_ai.Agent(model, instructions="You keep the user's notes.")
        self.harness = self.use(PydanticAIHarness("notes", agent, target=self))

    @tool
    async def add_note(self, text: str) -> str:
        """Save a note."""
        self.set_state({"notes": [*self.state["notes"], text]})
        return "Saved"

    async def on_request(self, request: Request) -> Response:
        result = await self.harness.prompt(await request.text())
        return Response(result.text or "")


class Default(WorkerEntrypoint):
    async def fetch(self, request: Request) -> Response:
        response = await route_agent_request(request, self.env)
        if response is None:
            return Response("Not found", status=404)
        return response
```

Add the Workers AI binding to your Wrangler config:

```jsonc
{
  "main": "src/entry.py",
  "compatibility_date": "2026-07-20",
  "compatibility_flags": ["python_workers", "python_workers_314"],
  "ai": { "binding": "AI", "remote": true },
  "durable_objects": {
    "bindings": [{ "class_name": "Notes", "name": "Notes" }]
  },
  "migrations": [{ "tag": "v1", "new_sqlite_classes": ["Notes"] }]
}
```

Then talk to it:

```sh
curl -d "Remember to buy milk" http://localhost:8787/agents/notes/demo
```

- `WorkersAIModel` calls Workers AI through the `AI` binding. Any other Pydantic AI model works too, such as `"openai:gpt-5"` with its provider package installed.
- `pydantic_ai.Agent` keeps the model, the instructions and any tools you register on it.
- `target=self` gives the model every `@tool` method on `Notes`. The method's name, signature and docstring describe the tool. The target is also the run's `deps`.
- `self.use(...)` installs the harness. Call it in `__init__`, before the agent starts.
- `prompt()` adds the prompt to the root session and waits for the result. The next prompt continues the same conversation.

## Use a plain Durable Object

The harness doesn't need the `Agent` class. Install it on a `DurableObject` with `Lifecycle`:

```python
import pydantic_ai
from workers import DurableObject, Request, Response, WorkerEntrypoint

from agents import Lifecycle, tool
from agents.harness.pydantic_ai import PydanticAIHarness, WorkersAIModel


class Notes(DurableObject):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        model = WorkersAIModel("@cf/qwen/qwen3-30b-a3b-fp8", binding=env.AI)
        agent = pydantic_ai.Agent(model, instructions="You keep the user's notes.")
        self.harness = PydanticAIHarness("notes", agent, target=self)
        self.lifecycle = Lifecycle(self).use(self.harness)

    async def on_start(self) -> None:
        self.lifecycle.sql("CREATE TABLE IF NOT EXISTS notes (text TEXT)")

    @tool
    async def add_note(self, text: str) -> str:
        """Save a note."""
        self.lifecycle.sql("INSERT INTO notes (text) VALUES (?)", text)
        return "Saved"

    async def ask(self, prompt: str) -> str:
        result = await self.harness.prompt(prompt)
        return result.text or ""

    async def alarm(self, alarm_info=None) -> None:
        await self.lifecycle.alarm()


class Default(WorkerEntrypoint):
    async def fetch(self, request: Request) -> Response:
        notes = self.env.Notes.getByName("demo")
        return Response(await notes.ask(await request.text()))
```

The Wrangler config is the same as the quickstart's.

- `Lifecycle(self).use(...)` installs the harness and returns the `Lifecycle`.
- `alarm` must call `self.lifecycle.alarm()`. The harness resumes interrupted turns from this alarm, so without it a crashed turn waits for the next prompt.
- `on_start` runs once per wake, after the harness starts.
- `self.lifecycle.sql` runs SQL on the object's database. There is no `self.state` here, because that comes from `Agent`.
- `ask` is a normal RPC method. `prompt()` starts the Lifecycle if it hasn't started yet.

## Approve tool calls

Set `requires_approval=True` to stop the turn before the tool runs:

```python
from agents import Approval, tool

@tool(requires_approval=True)
async def clear_notes(self) -> str:
    """Delete every note."""
    self.set_state({"notes": []})
    return "Cleared"

async def clear_with_approval(self) -> str | None:
    result = await self.harness.prompt("Delete all my notes")

    if result.status == "needs_input":
        answers = [Approval(id=request.id, approved=True) for request in result.requests]
        result = await self.harness.prompt(answers)

    return result.text
```

- The turn settles with the status `needs_input`, and `result.requests` lists the tool calls.
- Answer by submitting a list of `Approval`s. Pass `approved=False` and a `message` to tell the model why you refused.
- A tool the model expects the client to run has `kind="call"`. Answer it with `ToolOutput(id=request.id, value=...)`.

## Stream a response

`stream()` submits a prompt and yields its events as the turn runs:

```python
from agents import TextEvent

async def ask_and_broadcast(self, message_id: str, prompt: str) -> None:
    session = self.harness.session()

    async for event in session.stream(prompt, operation_id=message_id):
        if isinstance(event, TextEvent):
            self.broadcast(event.text)

    result = await session.wait(message_id)
```

- `stream()` yields `OperationEvent`, `TextEvent`, `ToolCallEvent` and `ToolResultEvent` for this operation, and ends when it settles.
- `operation_id` dedupes retries. Resubmitting an open operation follows it, and resubmitting a settled one yields nothing.
- `wait()` returns the final result, including any tool requests.
- `watch()` follows the whole session instead. It first yields a `SessionSnapshot` and never ends by itself.
- The harness doesn't store events. A client that reconnects doesn't get the text it missed.

## Keep separate conversations

The harness has one root session. Create more for separate conversations:

```python
session = await self.harness.sessions.create()
await session.prompt("Plan my week")

branch = await self.harness.sessions.fork(session.id)
await branch.prompt("Now plan it without meetings")
```

| Method                       | What it does                                    |
| ---------------------------- | ----------------------------------------------- |
| `harness.session(id)`        | Returns a handle on an existing session         |
| `harness.sessions.create()`  | Creates an empty session                        |
| `harness.sessions.fork(id)`  | Creates a session with a copy of `id`'s history |
| `harness.sessions.list()`    | Returns every session id, oldest first          |
| `session.pending()`          | Returns the open operations, oldest first       |
| `session.abort(id)`          | Stops one open operation, or all of them        |
| `session.retry(id)`          | Runs an `unanswered` operation again            |
| `session.reset()`            | Clears the history                              |

Each session runs one turn at a time. New prompts queue behind open work, or raise `SessionBusyError` if you submit with `when_busy="reject"`.

## Recover from a crash

The harness saves a checkpoint before each model request and each round of tool calls. After a crash, it resumes the turn from the last checkpoint.

A tool call that was running when the crash happened may or may not have taken effect, so the default is not to run it again. Mark a tool as safe to repeat with `replay="safe"`:

```python
@tool(replay="safe")
async def search_notes(self, query: str) -> list[str]:
    """Search the notes."""
    return [note for note in self.state["notes"] if query in note]
```

- `replay="safe"` runs an interrupted call again. A call that needs approval runs again only if the client approved it.
- `replay="unsafe"`, the default, tells the model the call may not have finished and to check before calling the tool again.

Two constructor options control recovery:

| Option         | Default | What it does                                                                                     |
| -------------- | ------- | ------------------------------------------------------------------------------------------------ |
| `max_restarts` | `5`     | How many times to resume a turn. After that, its operation settles `unanswered` with reason `"interrupted"`. |
| `heartbeat`    | `30`    | Seconds between checks on a session with open work. This is the longest a turn waits to resume.  |

## Operation statuses

| Status        | Meaning                                                                 |
| ------------- | ----------------------------------------------------------------------- |
| `queued`      | Waiting for earlier work in the session                                 |
| `running`     | The turn is running                                                     |
| `done`        | Finished. `result.text` has the answer                                  |
| `needs_input` | Stopped on tool calls. `result.requests` lists them                     |
| `unanswered`  | The turn raised or ran out of restarts. `result.reason` says why        |
| `aborted`     | `abort()` stopped it                                                    |

## Limitations

- Output is always text or tool requests. The harness overrides the agent's `output_type`, so structured output doesn't work yet.
- The harness always passes the target as `deps`. You can't override it yet.
- `WorkersAIModel` accepts text and image prompts. It rejects documents, audio and video.
- `WorkersAIModel`'s `clean_text` option doesn't run on streamed requests, and the harness always streams.
