---
title: "Run a Pydantic AI agent in a Durable Object"
description: "Run a Pydantic AI agent in Python on Cloudflare: install the harness, give the model tools, approve tool calls, stream responses and resume after a crash."
---

`PydanticAIHarness` runs a [Pydantic AI](https://ai.pydantic.dev/) agent inside your Durable Object. It stores the conversation in SQLite, queues prompts and resumes a turn after a crash. [How a harness runs turns](harness.md) explains the operation statuses and the recovery steps.

## Installation

!!! note
    Python 3.13 or 3.14 Workers are required. The harness doesn't run on 3.12.

Run the following in your terminal:
```sh
uv add "cf-agents[pydantic-ai]"
```

For Python 3.14 Workers, use uv 0.13.4 or later and a `compatibility_date` of 2026-09-08 or later. You only need the `python_workers_314` compatibility flag if you keep an older date.

For Python 3.13 Workers with pywrangler, add this to your `pyproject.toml`. pywrangler's default index stops at pydantic 2.10, which is too old for Pydantic AI 2.64.
```toml
[tool.uv.pip]
extra-index-url = ["https://index.pyodide.org/0.29.3"]
index-strategy = "unsafe-best-match"
```

It must go under `[tool.uv.pip]`. Under `[tool.uv]`, it breaks `uv sync`. pywrangler picks versions from both indexes but installs each package from the first index that has it. Without `index-strategy`, `pywrangler sync` fails with `there is no version of typing-extensions==4.16.0`.

The examples use the [crosswind-workers](../workers/overview.md) types: `DurableObjectContext` for `ctx`, and the `Env` class that [`wynd types`](../wynd/types.md) writes for `env`.

## Quickstart

This agent saves notes when the model asks it to:

```python
import pydantic_ai
from env import Env
from workers import Request, Response, WorkerEntrypoint
from workers.context import DurableObjectContext

from agents import Agent, route_agent_request, tool
from agents.harness.pydantic_ai import PydanticAIHarness, WorkersAIModel


class Notes(Agent):
    initial_state = {"notes": []}

    def __init__(self, ctx: DurableObjectContext, env: Env) -> None:
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
        if result.status != "done":
            return Response(f"{result.status}: {result.reason}", status=500)
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
  "compatibility_date": "2026-09-08",
  "compatibility_flags": ["python_workers"],
  "ai": { "binding": "AI", "remote": true },
  "durable_objects": {
    "bindings": [{ "class_name": "Notes", "name": "Notes" }]
  },
  "migrations": [{ "tag": "v1", "new_sqlite_classes": ["Notes"] }]
}
```

`"remote": true` makes `dev` call the real Workers AI service, which has no
local version. See
[Remote bindings](https://developers.cloudflare.com/workers/development-testing/#remote-bindings).

!!! tip
    Pydantic AI prints a startup banner that Wrangler logs as `[ERROR]`.
    Set `pydantic_ai.BANNER_ENABLED = False` at the top of `src/entry.py` to hide it.
    The `PYDANTIC_AI_NO_BANNER` variable doesn't work here, because Wrangler `vars` don't reach `os.environ`.

Then talk to it:

```sh
curl -d "Remember to buy milk" http://localhost:8787/agents/notes/demo
```

- `WorkersAIModel` calls Workers AI through the `AI` binding. Any other Pydantic AI model works too, such as `"openai:gpt-5"` with its provider package installed.
- `pydantic_ai.Agent` keeps the model, the instructions and any tools you register on it.
- `target=self` gives the model every `@tool` method on `Notes`. The method's name, signature and docstring describe the tool. The target is also the run's `deps`.
- `self.use(...)` installs the harness. Call it in `__init__`, before the agent starts.
- `prompt()` adds the prompt to the root session and waits for the result. The next prompt continues the same conversation.
- `prompt()` doesn't raise when a turn fails. The result settles `unanswered`, and `result.reason` holds the error.

## Where Pydantic AI comes in

Pydantic AI runs the agent loop. Crosswind makes that loop durable. `PydanticAIHarness` is the adapter between them, and each turn makes this call in `run_turn`:

```python
async with self.agent.iter(
    prompt,                               # None when resuming or answering tool calls
    message_history=messages,             # Loaded from the checkpoint, or else the history
    deferred_tool_results=results,        # The client's answers and any interrupted calls
    deps=self.target,
    toolsets=[self._toolset],             # Your @tool methods
    output_type=[str, DeferredToolRequests],
) as run:
    async for node in run:
        if isinstance(node, ModelRequestNode | CallToolsNode):
            await self._step(turn, run, node)  # Saves a checkpoint, then streams events
```

| Job                                                        | Who does it       |
| ---------------------------------------------------------- | ----------------- |
| Calling the model and deciding the next step               | Pydantic AI       |
| Building tool schemas from signatures and docstrings       | Pydantic AI       |
| Validating tool arguments and retrying on `ModelRetry`     | Pydantic AI       |
| Running tools and pausing on approvals                     | Pydantic AI       |
| The message format of the history and checkpoints          | Pydantic AI       |
| Storing sessions, operations, history and checkpoints      | `Harness`         |
| Queueing, dedupe, abort, retry and the wake job            | `Harness`         |
| Deciding what happens to interrupted tool calls            | `PydanticAIHarness` |

Each Crosswind type maps to one Pydantic AI type:

| Crosswind                       | Pydantic AI                                               |
| ------------------------------- | --------------------------------------------------------- |
| `@tool` method                  | `Tool` in a `FunctionToolset`                             |
| `ToolRequest`                   | An entry in `DeferredToolRequests.approvals` or `.calls`  |
| `Approval`, `ToolOutput`        | An entry in `DeferredToolResults.approvals` or `.calls`   |
| History and checkpoint bytes    | `ModelMessagesTypeAdapter` JSON                           |
| `TextInput`, `FileInput`        | `UserPromptPart` content: text, `BinaryContent` or a file URL type |
| `ChunkEvent` text and reasoning | `PartStartEvent` and `PartDeltaEvent` with text or thinking |
| `ChunkEvent` tool input         | `PartStartEvent` and `PartDeltaEvent` with a tool call      |
| `ChunkEvent` tool output        | `FunctionToolResultEvent`                                 |
| Transcript reply                | The `ModelResponse`s and tool returns since the last user prompt |

The harness overrides `output_type` and `deps`, and adds your `@tool` methods to the agent's toolsets. Every other setting on `pydantic_ai.Agent` applies as usual, such as `model_settings`, `retries` and the agent's own tools.

## Use a plain Durable Object

The harness doesn't need the `Agent` class. Install it on a `DurableObject` with `Lifecycle`:

```python
import pydantic_ai
from env import Env
from workers import DurableObject, Request, Response, WorkerEntrypoint
from workers.context import DurableObjectContext

from agents import Lifecycle, tool
from agents.harness.pydantic_ai import PydanticAIHarness, WorkersAIModel


class Notes(DurableObject[Env]):
    def __init__(self, ctx: DurableObjectContext, env: Env) -> None:
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
        if result.status != "done":
            raise RuntimeError(f"{result.status}: {result.reason}")
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

## Add tools the harness doesn't own

`@tool` methods are the usual choice. Register a tool on the Pydantic AI agent when it needs Pydantic AI's `RunContext`. `ctx.deps` is the harness target:

```python
import pydantic_ai
from pydantic_ai import ModelRetry, RunContext

agent = pydantic_ai.Agent("openai:gpt-5", instructions="You keep the user's notes.")


@agent.tool
async def count_notes(ctx: RunContext[object]) -> int:
    """Count the saved notes."""
    if not ctx.deps.state["notes"]:
        raise ModelRetry("There are no notes yet. Ask the user to add one.")
    return len(ctx.deps.state["notes"])
```

| What a tool does            | What happens                                                                    |
| --------------------------- | ------------------------------------------------------------------------------- |
| Raises `ModelRetry`         | Pydantic AI sends the message to the model, and the turn continues              |
| Raises any other exception  | The operation settles `unanswered`, with the error message as `result.reason`   |

`@tool` methods don't receive a `RunContext`. They use `self` instead. A tool registered on the agent never runs again after a crash, because it has no `replay` setting.

To have the client run a tool, declare it with Pydantic AI's `ExternalToolset`. The turn stops with a `kind="call"` request:

```python
import pydantic_ai
from pydantic_ai import ExternalToolset
from pydantic_ai.tools import ToolDefinition

from agents import ToolOutput

location = ToolDefinition(
    name="get_location",
    description="Return the user's city.",
    parameters_json_schema={"type": "object", "properties": {}},
)
agent = pydantic_ai.Agent("openai:gpt-5", toolsets=[ExternalToolset([location])])


async def ask_with_location(self, prompt: str) -> str | None:
    result = await self.harness.prompt(prompt)

    if result.status == "needs_input":
        answers = [ToolOutput(id=request.id, value="Paris") for request in result.requests]
        result = await self.harness.prompt(answers)

    return result.text
```

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
- A tool the client runs has `kind="call"`. Answer it with `ToolOutput(id=request.id, value=...)`.
- Answers can arrive in several operations, such as one per person approving. An operation that leaves requests open settles `done` without running a turn, and the operation that answers the last one continues the turn. The first answer to each request wins.
- Answer before you send a new prompt. Otherwise the prompt settles `unanswered`. `session.reset()` clears the history if you want to drop the open calls instead.

## Stream a response

`stream()` submits a prompt and yields its events as the turn runs:

```python
from agents import ChunkEvent
from agents.harness.transcript import TextDelta

async def ask_and_broadcast(self, message_id: str, prompt: str) -> None:
    session = self.harness.session()

    async for event in session.stream(prompt, operation_id=message_id):
        if isinstance(event, ChunkEvent) and isinstance(event.chunk, TextDelta):
            self.broadcast(event.chunk.delta)

    result = await session.wait(message_id)
```

- `stream()` yields `OperationEvent`, `RunStartEvent`, `ChunkEvent` and `RunEndEvent` for this operation, and ends when it settles.
- Each `ChunkEvent` carries one piece of the reply: text, reasoning, a tool call's input or a tool's output. The harness turns Pydantic AI's stream events into these chunks.
- `operation_id` dedupes retries. Resubmitting an open operation follows it, and resubmitting a settled one yields nothing.
- `wait()` returns the final result, including any tool requests.
- `watch()` follows the whole session instead. It first yields a `SessionSnapshot` with the transcript and the reply in progress, and never ends by itself.

## Read the transcript

The harness keeps a transcript next to the Pydantic AI history: one user message per prompt and one assistant message per turn, with text, reasoning, tool calls and their results as parts. [The transcript](harness.md#the-transcript) explains how it's written.

- A prompt can be a list of `TextInput` and `FileInput` parts. A `data:` URL becomes `BinaryContent`, and any other URL becomes `ImageUrl`, `AudioUrl`, `VideoUrl` or `DocumentUrl` by its media type.
- A file the model makes is kept as a `data:` URL of up to 768 KiB. A larger one shows as a `dropped-file` data part with its media type and size, and the history still has it.
- Each assistant message's `metadata` holds the model name, token usage and finish reason of the turn's last model response.
- A tool the client runs gets the turn's `participant` as its `owner`. Pass `participant=` to `submit` when several people share a session.

`session.reset(handoff)` clears the history and the transcript. With a handoff note, the new history starts with it as a system prompt, so the model reads the note before the next prompt.

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
| `harness.session(id)`        | Returns a handle. The first submit creates the session, so `id` can be your own key, like `"user-42"` |
| `harness.sessions.create()`  | Creates an empty session                        |
| `harness.sessions.fork(id)`  | Creates a session with a copy of `id`'s history and transcript |
| `harness.sessions.list()`    | Returns a `SessionInfo` per session, oldest first |
| `session.pending()`          | Returns the open operations, oldest first       |
| `session.abort(id)`          | Stops one open operation, or all of them        |
| `session.retry(id)`          | Runs an `unanswered` operation again, unless a later one has run |
| `session.reset(handoff)`     | Clears the history and the transcript           |

Each session runs one turn at a time. New prompts queue behind open work, or raise `SessionBusyError` if you submit with `when_busy="reject"`.

## Recover from a crash

The harness saves a checkpoint, the Pydantic AI message list as JSON, before each model request and each round of tool calls. After a crash, it loads those messages and continues `agent.iter` from there. [How a crash recovers](harness.md#how-a-crash-recovers) covers what wakes the object.

Where the crash lands decides what repeats:

| The turn was in   | On resume                                                                  |
| ----------------- | -------------------------------------------------------------------------- |
| A model request   | Pydantic AI sends the request again, and its chunks stream again in a new run |
| A round of tools  | Each call in the round gets one of the outcomes in the next table          |

A tool result isn't saved until the next model request's checkpoint. If a round has three calls and the crash lands during the third, all three count as interrupted, including the two that finished.

A tool call that was running may or may not have taken effect, so the default is not to run it again. Mark a tool as safe to repeat with `replay="safe"`:

```python
@tool(replay="safe")
async def search_notes(self, query: str) -> list[str]:
    """Search the notes."""
    return [note for note in self.state["notes"] if query in note]
```

| The interrupted call                                 | On resume                                  |
| ---------------------------------------------------- | ------------------------------------------ |
| `replay="safe"`, no approval needed                  | Runs again                                 |
| `replay="safe"`, the client approved it              | Runs again                                 |
| `replay="safe"`, needs approval the client didn't give | Reported as interrupted                  |
| `replay="unsafe"`, the default                       | Reported as interrupted                    |
| The client denied it or sent a `ToolOutput`          | Keeps the client's answer                  |
| Registered on the Pydantic AI agent                  | Reported as interrupted                    |

"Reported as interrupted" means the model receives this text as the tool's result:

```text
The agent restarted while this tool call was running, so it may or may not have taken effect. Check its effect before calling the tool again.
```

Two constructor options control recovery:

| Option         | Default | What it does                                                                                     |
| -------------- | ------- | ------------------------------------------------------------------------------------------------ |
| `max_restarts` | `5`     | How many times to resume a turn. After that, its operation settles `unanswered` with reason `"interrupted"`. |
| `heartbeat`    | `30`    | Seconds, or a `timedelta`, between checks on a session with open work. This is the longest a turn waits to resume. |

## Limitations

- Output is always text or tool requests. The harness overrides the agent's `output_type`, so structured output doesn't work yet.
- The harness always passes the target as `deps`. You can't override it yet.
- Each turn uses Pydantic AI's default limit of 50 model requests, and you can't change it yet. Going over raises `UsageLimitExceeded`, and the operation settles `unanswered`.
- `WorkersAIModel` accepts text and image prompts. It rejects documents, audio and video.
- `WorkersAIModel`'s `clean_text` option doesn't run on streamed requests, and the harness always streams.

## Troubleshooting

`result.reason` holds the error when an operation settles `unanswered`.

### Why does the reason say "Cannot provide a new user prompt when the message history contains unprocessed tool calls"?

The session's last operation stopped at `needs_input`, and you sent a prompt instead of answers. Answer `result.requests`, or call `session.reset()` to clear the history.

### Why is the reason `not-waiting`?

No request matches the answers: an `id` is wrong, each request already has an answer, or the session isn't waiting on tool calls. Answer with the ids in `result.requests`.

### Why is the reason `not-owner`?

A `ToolOutput` came from a different `participant` than the one who started the turn. Only that participant's client runs its tools.

### Why is the reason `interrupted`?

The turn was interrupted more than `max_restarts` times. A turn that keeps dying is usually out of memory or hitting a limit. Fix the cause, then call `session.retry(id)`, which resets the count.

### Why is the reason `out_of_memory`?

The alarm hit the memory limit 3 times in a row. Cut what the turn holds in memory, then call `session.retry(id)`.

### Why does `session.retry(id)` raise `ValueError: ... can't be retried after later operations ran`?

A later operation in the session has run, so the old checkpoint would drop its turn. Send the prompt again as a new operation.

### Why does `ImportError: agents.harness.pydantic_ai needs pydantic-ai-slim>=2.64` appear on Python 3.13 Workers?

pywrangler's default index has an older version. Add the `extra-index-url` from [Installation](#installation) and run `pywrangler sync` again.

### Why does `pywrangler sync` say there is no version of `typing-extensions==4.16.0`?

`index-strategy = "unsafe-best-match"` is missing from `[tool.uv.pip]`. Add it next to the `extra-index-url` from [Installation](#installation).

### Why does a crashed turn only resume on the next prompt?

On a plain Durable Object, `alarm` must call `self.lifecycle.alarm()`. `Agent` does this for you.

## Next steps

- [How a harness runs turns](harness.md) covers statuses, storage and what repeats after a crash.
- [Write your own harness](custom-harness.md) runs another framework or your own loop.
