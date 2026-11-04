# Self-healing agent

A [Pydantic AI](https://ai.pydantic.dev/) agent kills its own Durable Object in the middle of a tool call. The harness brings it back from its last checkpoint and finishes the task.

## Run it

You need [uv](https://docs.astral.sh/uv/) 0.13.4 or later and Node.js. The model runs on Workers AI, so log in first with `npx wrangler login`.

```sh
npm install
npm run dev
```

Open http://localhost:8787 and press Enter. The tool crashes the agent once by itself. Press **Crash** during the run to kill it again. After 5 restarts, the harness gives up and the run fails. **Reset** clears the trace and the conversation once a run has finished.

The page uses `useAgent` from the official `agents` package. It shows a bar for each instance of the Durable Object, with a gap for the downtime. Click a trace row, or use the arrow keys, to see the Python line that wrote it.

## What happens

1. The harness saves a checkpoint before the model request and before the tool call.
2. `migrate_database` records the crash and calls `ctx.abort()`, which resets the object and drops everything in memory.
3. The page's WebSocket reconnects and wakes the object. With no page open, the harness's alarm wakes it within 30 seconds.
4. The harness loads the checkpoint and runs the interrupted call again, because the tool has `replay="safe"`.
5. The model reads the tool's result and writes its answer.

With the default, `replay="unsafe"`, the harness doesn't repeat the call. It tells the model that the call may not have finished.
