# Harness transcript

**Decided; built.** The harness owns a native transcript: the messages a
session's participants see, stored beside the model's history and streamed as
chunks while a turn runs. This doc records the design (from the Channels plan,
`handoffs/CROS-3/channels.md` §10) and what landed in the phase-0 PRs.

Upstream reference: cloudflare/agents `455b47e`,
`packages/agents/src/experimental/channels/` (`protocol.ts`, `response.ts`,
`apply-chunks.ts`, `harness.ts`) and `packages/agents/src/harness/`
(`ai-sdk/`, `think/`).

---

## 1. Why

Before this change a harness stored only opaque history bytes and published
`TextEvent` / `ToolCallEvent` / `ToolResultEvent`, which were not stored.
A client that reconnected lost what it missed, and nothing could render a
session's conversation without parsing the subclass's history format.

Channels needs a transcript the harness owns: upstream's Channels gateway reads
`watch()`'s snapshot, follows `chunk` and `message` events, and treats
`TranscriptMessage` as the record of a conversation. Any other UI needs the
same thing. Phase 0 builds it in `agents.harness` and
`agents.harness.pydantic_ai` only, so it is useful without Channels and
Channels never becomes a dependency of the harness.

We're pre-1.0, so the change is in place: harness types and tables change with
no migration and no compatibility shims.

## 2. Upstream's draft `AgentHarness`

Upstream's `experimental/channels/harness.ts` (marked draft) is the interface
Channels serves:

```ts
interface AgentHarness { sessions: { create(); fork(from); list(): SessionInfo[] }; session(id?): HarnessSession }
interface HarnessSession {
  submit(input: (HarnessInput | ToolAnswer) & { from?: { participantId } },
         options?: { operationId?; whenBusy?: "followUp" | "steer" }): Promise<Receipt>;
  abort(operationId?); wait(operationId); reset(handoff?);
  watch(): Promise<{ state: { messages, pending, run?: { operations, partial? } }, start(listener), stop(), closed }>;
}
type SessionEvent = operation | run-start | run-end | chunk(ResponseChunk) | message(TranscriptMessage) | reset;
```

The harness owns the transcript; `HarnessConversations`
(`harness-conversations.ts`) turns session events into turn records, one
response per run, and transcript pushes. Upstream's AI SDK harness
(`harness/ai-sdk/`) is the reference for ids and grouping (§4).

## 3. Python API

### 3.1 Against upstream

| Upstream (`harness.ts`) | Python | Status |
| --- | --- | --- |
| `session(id?)`, `sessions.create/fork` | Unchanged; `fork` copies the transcript too | In |
| `sessions.list(): SessionInfo{id, parent?, busy}` | `list() -> Sequence[SessionInfo]`; `parent` column on `cf_agents_harness_sessions`, `busy` from open operations | In |
| `submit(input, { operationId })` dedupe | `submit(..., operation_id=)` | In |
| `HarnessInput.parts` (text, file) | `HarnessInput = str \| Sequence[InputPart] \| Sequence[ToolAnswer]`; `InputPart = TextInput \| FileInput(media_type, url, filename)` | In |
| `messageId` (default: operation id) | `submit(..., message_id=None)`, stored on the operation row | In |
| `from: { participantId }` | `submit(..., participant=None)`, stored on the operation row; stamps `owner` on client tool calls; a `ToolOutput` from anyone else settles `unanswered` (`"not-owner"`); first answer wins | In |
| One tool answer per operation | An answer operation that leaves requests open records its answers and settles `done` without a run; the continuation runs once every request has an answer | In |
| `whenBusy: "followUp"` | `when_busy="queue"` (same meaning); `"reject"` stays as a Python extra | In |
| `whenBusy: "steer"` | — | **Deferred** (§7) |
| `abort(operationId?)`, `wait(operationId)` | Unchanged | In |
| `reset(handoff?)` + `reset` event | `reset(handoff=None)` publishes `ResetEvent`; `Harness.reset_history(handoff) -> bytes \| None` seeds the new history (default: empty) | In |
| `watch()` → `{state, start, stop, closed}` | `watch()` stays an async iterator; its first item is `SessionSnapshot(messages, pending, run)`; cancelling the iterating task is `stop()` | In (Pythonic shape) |
| `SessionEvent` | `OperationEvent`, `RunStartEvent(operations)`, `RunEndEvent(operations)`, `ChunkEvent(operation_id, chunk)`, `MessageEvent(message)`, `ResetEvent` | In; `TextEvent`, `ToolCallEvent`, `ToolResultEvent` **removed** |
| `OperationStatus` `queued/placed/done/unanswered` | Python's six statuses kept | In (by decision) |
| Client tools advertised per input | — | **Deferred** (§7) |

`SessionSnapshot.messages` is the transcript since the newest reset, without
what the run in progress has added; that is `run.partial`, the run's chunks
so far. A watcher applies `run.partial` and the following `ChunkEvent`s
(`apply_chunks`) and never sees the reply twice. `stream()` yields the
operation's `OperationEvent`, `RunStartEvent`, `ChunkEvent` and
`RunEndEvent`; `MessageEvent` and `ResetEvent` are session-wide and reach
`watch()` only.

### 3.2 Transcript records

`agents/harness/transcript.py` ports upstream's transcript and response types
(`protocol.ts`), `applyChunks` (`apply-chunks.ts`) and the chunk grammar
(`response.ts`): `TranscriptMessage`, seven `MessagePart` classes, nineteen
`ResponseChunk` classes, `to_wire` / `message_from_wire` / `part_from_wire` /
`chunk_from_wire`, `apply_chunks`, and `ResponseGrammar`.

- They are not `agents.chat`'s `UIMessage` classes: a tool part is one
  `type: "tool"` class with a `state` and an `owner`, and a response has no
  start or finish chunk. The module is imported by path; its names would
  collide with chat's in the root `agents` namespace.
- The wire form is upstream's camelCase JSON. Goldens in
  `tests/harness/golden/` are generated from upstream's own `applyChunks` by
  `tests/harness/golden/tools/gen.mjs`.

### 3.3 The subclass contract

`run_turn` stays the one method a harness implements.

```python
@dataclass(slots=True, frozen=True, kw_only=True)
class Turn:
    session: str
    operation_id: str
    input: HarnessInput          # the user message, or every answer the turn continues from
    history: bytes | None
    checkpoint: bytes | None
    attempt: int
    tools: Sequence[ToolSpec]
    participant: str | None      # who started the turn
    emit: Callable[[ChunkEvent], None]
    save: SaveCheckpoint         # save(checkpoint, /, *, reply=None, metadata=None)


@dataclass(slots=True, frozen=True, kw_only=True)
class TurnResult:
    history: bytes
    reply: Sequence[MessagePart] = ()     # the assistant message's parts after this turn
    metadata: JSONObject | None = None
    status: Literal["done", "needs_input"] = "done"
    text: str | None = None
    requests: Sequence[ToolRequest] = ()
```

- The subclass reports **chunks** while it streams and **reply parts**
  whenever it persists: `turn.save(checkpoint, reply=parts)` after each step,
  and `TurnResult.reply` at the end. It builds the parts from the messages it
  already holds, so nothing re-parses `history`.
- `reply` is the whole assistant message. A continuation's reply includes the
  parts from before the tool answers.
- The base class owns the rest: message ids, the user message,
  `run-start`/`run-end`, the in-memory `partial` for `watch()`, storage, and
  `MessageEvent`s.

## 4. Ids and grouping

One user message and one assistant message per turn, as upstream's AI SDK
harness does; a continuation extends the turn's assistant message.

- **User message**: id = `message_id` or the operation id; parts from the
  input. Written when the operation starts running (upstream's `placed`), not
  when it's queued, so queued input never lands between a running turn's
  messages. Answer operations write no user message.
- **Assistant message**: id = `<operation id>:reply` for a prompt operation. An
  answer operation's continuation writes the session's latest assistant
  message instead, which is what Channels' `extends` names.
- **Answers on the transcript.** When an answer operation records its answers,
  the base class writes them onto the latest assistant message's tool parts
  (approval decision, client output) and publishes a `MessageEvent`, before
  any continuation runs.
- Every `save(..., reply=)` and the settlement write the current assistant
  message, so an aborted or crashed turn keeps its partial reply (upstream's
  "Stop, then reload" and "Attempt" behaviour).
- **Reset** clears the transcript; a `handoff` note becomes a `system`
  message, the new transcript's first.

Event order for one turn: `OperationEvent(queued)`, `MessageEvent` (user),
`OperationEvent(running)`, `RunStartEvent`, `ChunkEvent`s,
`MessageEvent` (assistant), `RunEndEvent`, `OperationEvent` (settled). A
resumed turn publishes a new `RunStartEvent`; part ids are unique within a
run, not across runs.

## 5. Storage and transactions

```sql
CREATE TABLE IF NOT EXISTS cf_agents_harness_messages (
  harness TEXT NOT NULL,
  session TEXT NOT NULL,
  id TEXT NOT NULL,
  seq INTEGER NOT NULL,             -- order within the session
  role TEXT NOT NULL CHECK (role IN ('system', 'user', 'assistant')),
  message TEXT NOT NULL,            -- TranscriptMessage JSON (wire form)
  operation_id TEXT,                -- the operation that last wrote it
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (harness, session, id)
) WITHOUT ROWID;
-- plus on cf_agents_harness_sessions: parent TEXT
-- plus on cf_agents_harness_operations: message_id TEXT, participant TEXT, answers TEXT
```

The full schemas are in [sql_schemas.md](./sql_schemas.md) §14. The KV key
`cf_agents:harness_schema_version` (= 1) is stamped on first start, like the
other owners' version keys.

The history bytes and the transcript rows are written in **the same
`transactionSync`**: the checkpoint and reply in `save`, the history and final
reply in the settlement, and history plus rows in `fork` (copy) and `reset`
(delete, then the handoff). A crash never leaves the transcript ahead of or
behind the model's context.

**Why stored rather than derived on read:**

1. **Only the write path knows the grouping.** Which operation produced which
   model messages, the caller's `message_id`, the participant, and which
   assistant message a continuation extends are facts of the write. The opaque
   history keeps none of them.
2. **Snapshots are hot and history is heavy.** Every connect, follow and wake
   reads a snapshot. Deriving means validating the whole history in Pyodide
   each time; a stored transcript is one indexed read.
3. **The transcript is the participants' record and the history is the
   model's context, and they may diverge on purpose.** Compaction or
   `reset_history` seeding can rewrite the history without rewriting what
   people saw.

The cost is a second copy of the conversation text, roughly doubling storage
per session. A message row is under the same 2 MB value limit as a checkpoint;
a reply over it fails its `save`.

## 6. `PydanticAIHarness` mapping

Against `pydantic-ai-slim` 2.65.0 (pin `>=2.64,<3`). Code:
`pydantic_ai/{harness,events,utils,answers}.py`.

**History → reply parts** (`utils.to_reply`; the turn's span is the messages
after the last `UserPromptPart` request):

| pydantic-ai | `MessagePart` | Lossy? |
| --- | --- | --- |
| `TextPart` | `text` | `citations` dropped |
| `ThinkingPart` | `reasoning`; `signature` and `provider_name` in `providerMetadata.pydanticAi` | No |
| `FilePart` / `BinaryContent` the model made | `file` with a `data:` URL up to `MAX_FILE_URL` (768 KiB); larger is a `dropped-file` data part | Size only |
| `ToolCallPart` | `tool`, `input-available`, `input = args_as_dict()` | Unparseable args become `output-error` |
| A call in `DeferredToolRequests.approvals` | `tool`, `approval-requested`, `approval.id = tool_call_id` | No |
| A call in `DeferredToolRequests.calls` | `tool`, `input-available`, `owner = turn.participant` | No |
| `ToolReturnPart` `success` | `output-available`, `output = to_jsonable_python(content)` | Non-JSON is stringified |
| `ToolReturnPart` `failed` / `interrupted` / `retried` | `output-error`, `errorText` | No |
| `ToolReturnPart` `denied` | `output-denied` (`approval.approved = false`, `reason`) | No |
| `RetryFeedbackPart` (output validation) | Dropped | Yes, invisible to participants |
| `NativeToolCallPart` / `NativeToolReturnPart` | `tool` with `providerExecuted: true` | No |
| `SystemPromptPart` | Only via `reset_history(handoff)`, as the base class's system message | By design |
| Compaction, instruction, tool-search, capability and settings parts | Dropped | Yes, internal |
| `usage`, `model_name`, `finish_reason` of the last response | Assistant message `metadata` | No |

User input (`utils.user_prompt`): `TextInput` is text; a `FileInput` with a
`data:` URL is `BinaryContent`, any other URL is `ImageUrl`, `AudioUrl`,
`VideoUrl` or `DocumentUrl` by media type.

**Stream events → chunks** (`events.to_chunks(event, ids)`; part ids are
`<node index>:<part index>` from a `PartIds`, unique within one run):

| pydantic-ai event | `ResponseChunk`s |
| --- | --- |
| `PartStartEvent` / `PartDeltaEvent` / `PartEndEvent` on text | `text-start` (+ `text-delta` for initial content) / `text-delta` / `text-end` |
| Same on thinking | `reasoning-start` / `-delta` / `-end` (`signature_delta` not streamed) |
| `PartStartEvent(ToolCallPart)` | `tool-input-start` (+ `tool-input-delta` if `args` is a string) |
| `ToolCallPartDelta` with `args_delta: str` | `tool-input-delta` |
| `ToolCallPartDelta` with `args_delta: dict` | Nothing until the end (live display only) |
| `PartEndEvent(ToolCallPart)` | `tool-input-available` |
| `FunctionToolCallEvent(args_valid=False)` | `tool-input-error` |
| `FunctionToolResultEvent` | `tool-output-available`, `tool-output-error` or `tool-output-denied` by outcome |
| Native tool call / return parts | `tool-input-available` / `tool-output-available` with `providerExecuted` |
| `PartStartEvent(FilePart)` | `file` (same cap) |
| `DeferredToolRequestsEvent` | `tool-approval-request` per approval |
| `FinalResultEvent`, output-tool, realtime and capability events | None |

One live-only loss: `owner` on a client call is known only once the run ends
with `DeferredToolRequests`, so it's on the saved message, not the earlier
`tool-input-start`. Channels reads ownership from the transcript, as upstream
does.

`reset_history(handoff)` returns a history of one `ModelRequest` with a
`SystemPromptPart`. The harness adds the agent's `system_prompt` only to an
empty history, so after a reset with a handoff only the agent's
`instructions` apply alongside the note.

## 7. Deferred

- **`steer`.** pydantic-ai 2.65 can't inject a user message into a running
  `Agent.iter`; a steer would end the run at the next node boundary and
  re-run. Not worth it before a user needs it.
- **Client tools per input.** Upstream lets an input advertise the client's
  tools. The harness still takes client tools from the agent's toolsets.
- **Citations as sources.** `TextPart.citations` could become `source-url`
  parts.
- **`ConversationHarness`** (Channels plan §5.4) belongs to the Channels
  phases, not here.

## 8. Implementation notes

Phase 0 landed as four stacked PRs: #60 (transcript records), #61 (harness
base), #63 (`PydanticAIHarness`), and the docs PR with this doc.

- **Errors.** `ResponseGrammarError` lives in `agents.harness.errors` (an
  `AgentsException`), not a Channels error type, so the harness has no
  Channels dependency.
- **Own codec.** The transcript records have their own wire codec in
  `transcript.py` rather than sharing chat's text-delta codec; the shapes
  differ (§3.2).
- **`metadata`.** `save` and `TurnResult` take a `metadata` JSON object for
  the assistant message, which the Pydantic AI port uses for model name,
  usage and finish reason. The plan's contract had `reply` only.
- **`RetryPromptPart`.** Deprecated in pydantic-ai 2.64+. A retried tool call
  is a `ToolReturnPart(outcome="retried")`, mapped to `output-error`; the
  output-validation `RetryFeedbackPart` is dropped. Tests run with
  `-W error::DeprecationWarning`.
- **`to_chunks` takes a `PartIds`** that tracks the node and the tool call id
  of each part index, since a delta event carries only the index.
- **The base class doesn't check the chunk grammar at `emit`.** Harness
  tests validate a run's chunks with `ResponseGrammar`; enforcing it at
  runtime would make a subclass bug fail the turn.
- **Tests.** `tests/harness/test_transcript.py` (goldens and `apply_chunks`
  parity), `test_harness.py` (`ScriptedHarness`: grouping, continuation,
  partial reply after abort and crash, fork and reset, owner and partial
  answers, a failed save or settlement writing neither half), `test_pydantic_ai.py` and `test_pydantic_workers_ai.py`
  (`TestModel` / `FunctionModel` runs across text, thinking, tools,
  approval, deferred calls, denial, retry and native tools, with the grammar
  checked).
