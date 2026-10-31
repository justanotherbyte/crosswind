"""Upstream ``chat/resumable-stream.ts``.

Also ports ``chat/replay-frames.ts`` and ``chat/chunk-size.ts``.

Each turn's output is one stream in the shared chunk log, marked as chat's by
``cfChat`` in its metadata, so other producers' streams on the same object
are never touched. Chunks are buffered into packed segments to save storage
writes.

The interface is synchronous, so the agent can drive it from inside a SQLite
transaction.

Not yet ported (12d, recovery): the progress marker (``cf_agents_chat_progress``)
and the stream lookups recovery uses.
"""

import json
import logging
import secrets
from collections import OrderedDict
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Final, cast

from ..core.encoding import byte_length
from ..core.timing import now_ms
from ..core.types import JSONValue
from ..streams.errors import StreamClosedError
from ..streams.streams import Streams
from ..streams.types import StreamRow, StreamState
from ..websockets.connection import Connection
from ._json import dumps
from .protocol import USE_CHAT_RESPONSE, ChatTurnOutcome, send_if_open

__all__ = (
    "CHAT_STREAM_MAX_CHUNK_BYTES",
    "CHUNK_MAX_BYTES",
    "ResumableStream",
    "create_chat_streams",
    "stored_chunk_bytes",
)

_log = logging.getLogger("agents.chat")

# A chunk over the row limit is sent live but not stored.
CHUNK_MAX_BYTES: Final = 1_800_000
CHAT_STREAM_MAX_CHUNK_BYTES: Final = 1_900_000

_SEGMENT_CHUNKS = 10
_BUFFER_MAX_CHUNKS = 100
_SEGMENT_MAX_BYTES = 512_000
_REPLAY_PAGE_SEGMENTS = 10
_ABANDONED_RETENTION_MS = 60 * 60 * 1000
_REMEMBERED_DELETED_TERMINALS = 32


def create_chat_streams() -> Streams:
    return Streams(max_chunk_bytes=CHAT_STREAM_MAX_CHUNK_BYTES)


def stored_chunk_bytes(body: str) -> int:
    return byte_length(dumps(body))


@dataclass(slots=True, frozen=True)
class _Terminal:
    message_ids: Sequence[str] | None
    outcome: ChatTurnOutcome | None


class ResumableStream:
    def __init__(self, streams: Streams) -> None:
        self._streams = streams
        self._active_stream_id: str | None = None
        self._active_request_id: str | None = None
        # Whether the active stream was started here (``False`` when it was
        # restored from storage: nothing is producing it any more).
        self._is_live = False
        self._active_is_continuation = False
        # The replay index of the next stored chunk (``None`` when restored).
        self._next_chunk_seq: int | None = None
        self._buffer: list[tuple[str, str]] = []
        self._buffer_bytes = 0
        self._pending_cutover: str | None = None
        self._last_closed_stream_id: str | None = None
        self._deleted_terminals: OrderedDict[str, _Terminal] = OrderedDict()
        streams._ensure_tables()
        self._delete_hook = streams._on_delete(self._on_row_deleted)
        self.restore()

    @property
    def active_stream_id(self) -> str | None:
        return self._active_stream_id

    @property
    def active_request_id(self) -> str | None:
        return self._active_request_id

    def has_active_stream(self) -> bool:
        return self._active_stream_id is not None

    @property
    def is_live(self) -> bool:
        return self._is_live

    @property
    def pending_cutover_id(self) -> str | None:
        return self._pending_cutover

    def start(
        self,
        request_id: str,
        *,
        message_id: str | None = None,
        continuation: bool = False,
        origin_message_ids: Sequence[str] | None = None,
    ) -> str:
        self.flush_buffer()
        seq_base = self._next_seq_for_request(request_id)
        self.reclaim()
        stream_id = secrets.token_urlsafe(16)
        self._active_stream_id = stream_id
        self._active_request_id = request_id
        self._is_live = True
        self._active_is_continuation = continuation
        self._next_chunk_seq = seq_base
        metadata: dict[str, JSONValue] = {"cfChat": 1}
        if message_id is not None:
            metadata["messageId"] = message_id
        if continuation:
            metadata["isContinuation"] = 1
        if seq_base > 0:
            metadata["seqBase"] = seq_base
        if origin_message_ids:
            metadata["originMessageIds"] = list(origin_message_ids)
        self._streams._insert_stream(stream_id, request_id, metadata)
        return stream_id

    def complete(self, stream_id: str, outcome: ChatTurnOutcome | None = None) -> None:
        self.flush_buffer()
        orphaned = stream_id == self._active_stream_id and not self._is_live
        self._record_outcome(stream_id, outcome or ("aborted" if orphaned else None))
        self._streams._settle(stream_id, "completed", None)
        if self._pending_cutover == stream_id:
            self._pending_cutover = None
        self._last_closed_stream_id = stream_id
        self._clear_active()

    # Leaves the stream live until `cutover` or `finalize_pending` settles it, so
    # a crash keeps the evidence recovery rebuilds the message from.
    def finish(self, stream_id: str, outcome: ChatTurnOutcome | None = None) -> None:
        self.flush_buffer()
        self._record_outcome(stream_id, outcome)
        self._pending_cutover = stream_id
        self._last_closed_stream_id = stream_id
        self._clear_active()

    # One transaction, so a crash leaves either the live stream or the saved
    # message, never neither.
    def cutover(
        self, stream_id: str, persist: Callable[[], None], *, discard: bool = True
    ) -> None:
        self.flush_buffer()
        if not self._streams._settle(
            stream_id, "completed", None, commit=persist, discard=discard
        ):
            persist()
        if self._pending_cutover == stream_id:
            self._pending_cutover = None
        self._clear_active()

    def finalize_pending(self) -> None:
        stream_id = self._pending_cutover
        if stream_id is None:
            return
        self._streams._settle(stream_id, "completed", None)
        self._pending_cutover = None

    def mark_error(self, stream_id: str) -> None:
        self.flush_buffer()
        self._streams._settle(stream_id, "errored", None)
        if self._pending_cutover == stream_id:
            self._pending_cutover = None
        self._last_closed_stream_id = stream_id
        self._clear_active()

    def _clear_active(self) -> None:
        self._active_stream_id = None
        self._active_request_id = None
        self._is_live = False
        self._active_is_continuation = False

    def store_chunk(self, stream_id: str, body: str) -> int | None:
        size = stored_chunk_bytes(body)
        if size > CHUNK_MAX_BYTES:
            _log.warning(
                "Not storing a %d-byte chat chunk (over the row limit); "
                "live clients still receive it",
                size,
            )
            return None
        seq = None
        if stream_id == self._active_stream_id and self._next_chunk_seq is not None:
            seq = self._next_chunk_seq
            self._next_chunk_seq += 1
        if len(self._buffer) >= _BUFFER_MAX_CHUNKS:
            self.flush_buffer()
        if self._buffer and self._buffer_bytes + size > _SEGMENT_MAX_BYTES:
            self.flush_buffer()  # a large chunk starts (and is) its own segment
        self._buffer.append((stream_id, body))
        self._buffer_bytes += size
        if len(self._buffer) >= _SEGMENT_CHUNKS:
            self.flush_buffer()
        return seq

    def flush_buffer(self) -> None:
        if not self._buffer:
            return
        buffered, self._buffer, self._buffer_bytes = self._buffer, [], 0
        # start() flushes before switching streams: one stream per buffer.
        stream_id = buffered[0][0]
        segment: JSONValue = (
            buffered[0][1] if len(buffered) == 1 else [body for _, body in buffered]
        )
        # A stream that settled or was deleted meanwhile drops its buffered chunks.
        with suppress(StreamClosedError):
            self._streams._append(stream_id, segment)

    def replay_chunks(self, connection: Connection, request_id: str) -> str | None:
        stream_id = self._active_stream_id
        if stream_id is None:
            return None
        self.flush_buffer()
        continuation = self._active_is_continuation
        if not _send_replay_bodies(
            connection,
            request_id,
            self._stored_bodies(stream_id),
            continuation=continuation,
            first_seq=self._seq_base(stream_id),
        ):
            return None  # closed mid-replay, so the stream stays for the next try
        # Nothing produces a restored stream any more, so it ends here and the
        # agent saves its partial message.
        if not self._is_live:
            row = self._streams._get_stream(stream_id)
            chat = _chat_metadata(row) if row is not None else None
            _send_replay_control(
                connection,
                request_id,
                done=True,
                continuation=continuation,
                message_ids=_origin_ids(chat),
                outcome="aborted",
            )
            self.complete(stream_id, "aborted")
            return stream_id
        # Live frames follow.
        _send_replay_control(
            connection,
            request_id,
            done=False,
            replay_complete=True,
            continuation=continuation,
        )
        return None

    def replay_completed_chunks_by_request_id(
        self, connection: Connection, request_id: str
    ) -> bool:
        self.flush_buffer()
        row = self._latest_chat_row_by_tag(request_id, "completed")
        if row is None:
            return False
        chat = _chat_metadata(row)
        continuation = _is_continuation(chat)
        if not _send_replay_bodies(
            connection,
            request_id,
            self._stored_bodies(row["stream_id"]),
            continuation=continuation,
            first_seq=_seq_base(chat),
        ):
            return False
        return _send_replay_control(
            connection,
            request_id,
            done=True,
            continuation=continuation,
            message_ids=_origin_ids(chat),
            outcome=_outcome(chat),
        )

    def replay_closed_stream_chunks(
        self, connection: Connection, request_id: str
    ) -> bool:
        self.flush_buffer()
        row = (
            self._streams._get_stream(self._last_closed_stream_id)
            if self._last_closed_stream_id is not None
            else None
        )
        chat = (
            _chat_metadata(row)
            if row is not None and row["tag"] == request_id
            else None
        )
        continuation = _is_continuation(chat)
        if (
            row is not None
            and chat is not None
            and not _send_replay_bodies(
                connection,
                request_id,
                self._stored_bodies(row["stream_id"]),
                continuation=continuation,
                first_seq=_seq_base(chat),
            )
        ):
            return False
        return _send_replay_control(
            connection,
            request_id,
            done=False,
            replay_complete=True,
            continuation=continuation,
        )

    def replay_errored_chunks_by_request_id(
        self, connection: Connection, request_id: str
    ) -> bool:
        self.flush_buffer()
        row = self._latest_chat_row_by_tag(request_id, "errored")
        if row is None:
            return True  # only a closed connection fails, the error frame follows
        chat = _chat_metadata(row)
        return _send_replay_bodies(
            connection,
            request_id,
            self._stored_bodies(row["stream_id"]),
            continuation=_is_continuation(chat),
            first_seq=_seq_base(chat),
        )

    def stream_chunks(self, stream_id: str) -> list[str]:
        return list(self._stored_bodies(stream_id))

    def get_stream_message_id(self, stream_id: str) -> str | None:
        row = self._streams._get_stream(stream_id)
        chat = _chat_metadata(row) if row is not None else None
        message_id = chat.get("messageId") if chat is not None else None
        return message_id if isinstance(message_id, str) else None

    def get_origin_message_ids(self, request_id: str) -> Sequence[str] | None:
        row = self._latest_chat_row_by_tag(request_id)
        ids = _origin_ids(_chat_metadata(row)) if row is not None else None
        if ids is not None:
            return ids
        deleted = self._deleted_terminals.get(request_id)
        return deleted.message_ids if deleted is not None else None

    def get_outcome(self, request_id: str) -> ChatTurnOutcome | None:
        row = self._latest_chat_row_by_tag(request_id)
        outcome = _outcome(_chat_metadata(row)) if row is not None else None
        if outcome is not None:
            return outcome
        deleted = self._deleted_terminals.get(request_id)
        return deleted.outcome if deleted is not None else None

    def restore(self) -> None:
        for row, chat in self._chat_rows():
            if row["state"] == "streaming":
                self._active_stream_id = row["stream_id"]
                self._active_request_id = row["tag"]
                self._active_is_continuation = _is_continuation(chat)
                self._next_chunk_seq = None
                return

    def clear_all(self) -> None:
        self._buffer, self._buffer_bytes = [], 0
        self._streams._delete_many([row["stream_id"] for row, _ in self._chat_rows()])
        self._deleted_terminals.clear()
        self._last_closed_stream_id = None
        self._clear_active()

    def reclaim(self, now: int | None = None) -> int:
        # Silence is measured from the newest chunk. The row's ``updated_at`` is
        # only a first, cheap filter.
        cutoff = (now_ms() if now is None else now) - _ABANDONED_RETENTION_MS
        reclaimable = [
            row["stream_id"]
            for row, _ in self._chat_rows()
            if row["state"] != "streaming"
            or (
                row["stream_id"] != self._active_stream_id
                and row["updated_at"] < cutoff
                and (
                    self._streams._last_chunk_at(row["stream_id"]) or row["updated_at"]
                )
                < cutoff
            )
        ]
        self._streams._delete_many(reclaimable)
        return len(reclaimable)

    def dispose(self) -> None:
        self._delete_hook.dispose()

    def _stored_bodies(self, stream_id: str) -> Iterator[str]:
        # Paged, so a large turn is replayed one page of segments at a time.
        start = 0
        while True:
            rows = self._streams._read_chunks(stream_id, start, _REPLAY_PAGE_SEGMENTS)
            for row in rows:
                start = row["seq"] + 1
                segment = json.loads(row["chunk"])
                if isinstance(segment, list):
                    yield from segment
                else:
                    yield segment
            if len(rows) < _REPLAY_PAGE_SEGMENTS:
                return

    def _next_seq_for_request(self, request_id: str) -> int:
        # A request that restarts its stream continues its earlier sequence.
        prior = self._latest_chat_row_by_tag(request_id)
        if prior is None:
            return 0
        count = sum(1 for _ in self._stored_bodies(prior["stream_id"]))
        return _seq_base(_chat_metadata(prior)) + count

    def _seq_base(self, stream_id: str) -> int:
        row = self._streams._get_stream(stream_id)
        return _seq_base(_chat_metadata(row)) if row is not None else 0

    def _chat_rows(self) -> Iterator[tuple[StreamRow, dict[str, Any]]]:
        for row in self._streams._list_rows():
            chat = _chat_metadata(row)
            if chat is not None:
                yield row, chat

    def _latest_chat_row_by_tag(
        self, request_id: str, state: StreamState | None = None
    ) -> StreamRow | None:
        # Tags aren't unique and the table is shared: ownership is cfChat.
        return next(
            (
                row
                for row in self._streams._rows_by_tag(request_id, state)
                if _chat_metadata(row) is not None
            ),
            None,
        )

    def _record_outcome(self, stream_id: str, outcome: ChatTurnOutcome | None) -> None:
        if outcome is None or outcome == "completed":
            return
        row = self._streams._get_stream(stream_id)
        chat = _chat_metadata(row) if row is not None else None
        if chat is not None:
            self._streams._set_metadata(stream_id, {**chat, "outcome": outcome})

    def _on_row_deleted(self, row: StreamRow, _cursor: int) -> None:
        # Remember a deleted stream's terminal details for a late resume ACK.
        chat = _chat_metadata(row)
        tag = row["tag"]
        if (
            chat is None
            or not tag
            or ("originMessageIds" not in chat and "outcome" not in chat)
        ):
            return
        self._deleted_terminals.pop(tag, None)
        self._deleted_terminals[tag] = _Terminal(_origin_ids(chat), _outcome(chat))
        if len(self._deleted_terminals) > _REMEMBERED_DELETED_TERMINALS:
            self._deleted_terminals.popitem(last=False)


def _chat_metadata(row: StreamRow) -> dict[str, Any] | None:
    text = row["metadata"]
    if text is None:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) and parsed.get("cfChat") == 1 else None


def _is_continuation(chat: dict[str, Any] | None) -> bool:
    return chat is not None and chat.get("isContinuation") == 1


def _seq_base(chat: dict[str, Any] | None) -> int:
    base = chat.get("seqBase") if chat is not None else None
    return base if isinstance(base, int) else 0


def _origin_ids(chat: dict[str, Any] | None) -> Sequence[str] | None:
    ids = chat.get("originMessageIds") if chat is not None else None
    return cast(list[str], ids) if isinstance(ids, list) else None


def _outcome(chat: dict[str, Any] | None) -> ChatTurnOutcome | None:
    return chat.get("outcome") if chat is not None else None


# Replay frames (upstream chat/replay-frames.ts)


def _send_replay_bodies(
    connection: Connection,
    request_id: str,
    bodies: Iterable[str],
    *,
    continuation: bool,
    first_seq: int = 0,
) -> bool:
    for seq, body in enumerate(bodies, start=first_seq):
        frame: dict[str, Any] = {
            "body": body,
            "done": False,
            "id": request_id,
            "type": USE_CHAT_RESPONSE,
            "replay": True,
            "seq": seq,
        }
        if continuation:
            frame["continuation"] = True
        if not send_if_open(connection, frame):
            return False
    return True


def _send_replay_control(
    connection: Connection,
    request_id: str,
    *,
    done: bool,
    continuation: bool,
    replay_complete: bool = False,
    message_ids: Sequence[str] | None = None,
    outcome: ChatTurnOutcome | None = None,
) -> bool:
    frame: dict[str, Any] = {
        "body": "",
        "done": done,
        "id": request_id,
        "type": USE_CHAT_RESPONSE,
        "replay": True,
    }
    if replay_complete:
        frame["replayComplete"] = True
    if continuation:
        frame["continuation"] = True
    if done and message_ids:
        frame["messageIds"] = list(message_ids)
    if done and outcome:
        frame["outcome"] = outcome
    return send_if_open(connection, frame)
