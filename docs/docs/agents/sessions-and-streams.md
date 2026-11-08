---
title: "Store conversations and durable streams in an agent"
description: "Use the Streams and Sessions capabilities in a Python agent on Cloudflare to replay and follow a chunk log and keep chat history without AIChatAgent."
---

`AIChatAgent` is built on two capabilities that you can use by themselves: `Streams` and `Sessions`. Reach for them when you want part of what a chat agent does, but your own protocol or your own turn logic.

| You need                                                         | Use |
| ---------------------------------------------------------------- | --- |
| Output that a client can reconnect to and replay from where it left off | `Streams` |
| A conversation history you read, edit and trim                   | `Sessions` |
| All of that, wired up to `useAgentChat`                          | [`AIChatAgent`](chat.md) |
| An agent loop that survives a crash                              | [A harness](harness.md) |

Neither one needs an alarm, so both work on [sub-agents](sub-agents.md) too. An `AIChatAgent` already has them at `self.streams` and `self.sessions`, so installing another raises `RuntimeError`.

## Streams

A stream is an ordered log of JSON chunks, stored in SQLite as they're written. Readers get everything so far, then follow new chunks live.

```python
from typing import Any

from agents import Agent, Streams, callable


class Reports(Agent):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        self.streams = self.use(Streams())

    @callable
    async def write(self, report_id: str) -> None:
        writer = await self.streams.open(report_id, tag="report")
        for section in ("summary", "details"):
            writer.append({"section": section})
        writer.close()

    @callable
    async def read(self, report_id: str, start: int = 0) -> list[dict[str, Any]]:
        return [
            {"seq": chunk.seq, "chunk": chunk.chunk}
            async for chunk in self.streams.read(report_id, start=start)
        ]
```

After `write("r1")`, `read("r1")` returns both chunks, and `read("r1", start=1)` returns only `{"seq": 1, "chunk": {"section": "details"}}`.

The idea is that a client remembers the last `seq` it saw. When it reconnects, it reads from `seq + 1` and misses nothing.

### Write to a stream

`open` returns a `StreamWriter`. Reopening a stream that's still live gives you a writer at its current position, which is how a producer picks up after a crash.

| Member | Description |
| ------ | ----------- |
| `append(chunk)` | Stores `chunk` and wakes live readers. Returns its `seq`, starting from 0. It's synchronous, so don't `await` it. |
| `cursor` | The next `seq` to be assigned. |
| `close(*, commit=None, discard=False)` | Settles the stream as `completed`. |
| `error(reason=None, *, commit=None, discard=False)` | Settles it as `errored`, and records `reason`. |

`commit` is a function of synchronous writes, such as saving the finished message, that land in one transaction with the settle. If it raises, nothing is saved and the stream stays live. `discard=True` deletes the stream's rows in that same transaction.

!!! warning
    Nothing settles a stream for you. If the producer dies, the stream stays `streaming` and its readers wait. Check `status(id).updated_at` to spot one that's gone quiet, then reopen it and finish the job, or settle it.

### Read a stream

| Method | Description |
| ------ | ----------- |
| `read(id, *, start=0)` | Yields each `StreamChunk`, with `seq` and `chunk`. |
| `read_batches(id, *, start=0, batch_size=100, on_up_to_date=None)` | The same, in batches. Use this if each write out costs you something, like a socket send. `on_up_to_date` is a plain function, called once when the reader first catches up. |
| `async status(id)` | A `StreamStatus`, or `None` if there's no such stream. |
| `async list(*, state=None, tag=None, limit=None)` | Streams, newest first, 100 by default. `list(tag="report", limit=1)` finds the newest stream for a tag. |
| `async delete(id)` | Deletes a settled stream. Returns `False` if there's none. Raises `RuntimeError` on a live one. |

A read ends once the stream settles and every chunk has been yielded. That includes an `errored` stream, so check `status` to see how it ended. Deleting a stream, which `discard=True` also does, ends its readers early with no error, and `status` then returns `None`.

Live readers run in the same isolate as the producer. If the isolate dies, they need to read again from their last `seq + 1`.

### Limits and errors

- One chunk, and a stream's metadata, can be at most 1 MiB. Set `Streams(max_chunk_bytes=...)` to change it.
- A stream id is 1 to 256 characters.
- Nothing deletes old streams for you. Call `delete` when you're done with them.

| Exception | Raised when |
| --------- | ----------- |
| `StreamClosedError` | You `open` a settled stream, or `append` to one that's been settled or deleted. |
| `StreamNotFoundError` | You `read` a stream that doesn't exist. |
| `StreamSerializationError` | A chunk or metadata isn't JSON, or is over the limit. |
| `ValueError` | The id is empty or too long, or a live stream has a different `tag`. |

## Sessions

A session is one conversation, stored as a chain of messages in SQLite. Messages use the AI SDK's `UIMessage` shape, so they match what [`AIChatAgent`](chat.md) stores.

```python
from agents import Agent, Sessions, callable


class Conversations(Agent):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        self.sessions = self.use(Sessions())

    @callable
    async def add(self, user_id: str, message_id: str, text: str) -> bool:
        result = await self.sessions.session(user_id).append_message(
            {
                "id": message_id,
                "role": "user",
                "parts": [{"type": "text", "text": text}],
            }
        )
        return result.inserted

    @callable
    async def recent(self, user_id: str) -> list[str]:
        recent = await self.sessions.session(user_id).get_recent_history(16_000)
        return [message["id"] for message in recent.messages]
```

Calling `add("ada", "m1", "hello")` returns `True`. Calling it again with `m1` returns `False` and writes nothing, so a client can safely retry. `recent("ada")` returns the newest message ids that fit in 16,000 bytes, oldest first.

`session(id)` makes no database calls, so you can call it freely. `session()` with no id is the default session, which is all you need for one conversation per agent.

### Read and write messages

| Method on `Session` | Description |
| ------------------- | ----------- |
| `async append_message(message, *, source="server")` | Adds `message` after the newest one. Returns an `AppendResult`, with `inserted` and the stored `message`. |
| `async update_message(message, *, source="server")` | Replaces the message with that id. Returns it, or `None` if there's no such id. |
| `async upsert_message(message, *, source="server")` | Appends if the id is new, otherwise updates. |
| `async delete_messages(ids)` | Deletes messages. The ones after them stay, and follow their parent. |
| `async clear_messages()` | Deletes every message in the session. |
| `history()` | Yields the conversation, oldest first. |
| `history_batches(*, batch_size=50, max_batch_bytes=4194304)` | The same, in batches. |
| `async get_history()` | The whole conversation, as a list. |
| `async get_recent_history(max_content_bytes)` | A `RecentHistoryResult`, with `messages`, `truncated` and `total_content_bytes`. The newest message is always included, however large. |
| `async get_history_row_stats()` | Each message's `id`, `role`, `bytes` and `token_estimate`, with no content. |
| `mirror(*, get, set, transform=None)` | Keeps an in-memory list in step with this session's writes. Dispose the result to stop. |

Prefer `history` or `get_recent_history` to `get_history` for a long conversation. SQLite reads share the isolate's memory, which is why `history` fetches its content in bounded windows.

Messages too large for one row are split across several for you. Part types the SDK doesn't recognise are stored as they are.

### Protect metadata from clients

Pass `source="client"` when a message comes from a client. That strips any metadata key you listed as reserved, so a client can't set them:

```python
self.sessions = self.use(Sessions(reserved_metadata_keys=("cost",)))
```

An update replaces the whole message, so a `source="client"` update or upsert also erases what's stored under those keys. Merge the stored value back in if you need to keep it.

### Follow every write

`self.sessions.subscribe(listener)` calls `listener`, which can be sync or async, after each write across all sessions. It gets an `AppendEvent`, `UpdateEvent`, `DeleteEvent` or `ClearEvent`. Dispose the result to unsubscribe. An append of an id that already exists wrote nothing, but it still sends an `AppendEvent`, with `inserted=False`.

A listener that raises is logged and reported as a `session:error` event. The write has already succeeded by then, and the other listeners still run. Listeners live in memory, so subscribe again after a wake, in `on_start`.
