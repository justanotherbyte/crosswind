"""Upstream ``chat/recovery-incident.ts``, its ``cf:chat:last-terminal`` record.

A turn that fails while no client is connected leaves its error here, so the
resume handshake can deliver it when one reconnects. A later turn that
completes or is stopped clears it.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

__all__ = ("TerminalRecord", "clear_terminal", "pending_terminal", "record_terminal")

_KEY: Final = "cf:chat:last-terminal"


@dataclass(slots=True, frozen=True)
class TerminalRecord:
    request_id: str
    body: str
    message_ids: Sequence[str] | None = None


async def record_terminal(storage: Any, record: TerminalRecord) -> None:
    value: dict[str, Any] = {"requestId": record.request_id, "body": record.body}
    if record.message_ids:
        value["messageIds"] = list(record.message_ids)
    await storage.put(_KEY, json.dumps(value))


async def clear_terminal(storage: Any) -> None:
    await storage.delete(_KEY)


async def pending_terminal(storage: Any) -> TerminalRecord | None:
    stored = await storage.get(_KEY)
    if not isinstance(stored, str):
        return None
    value = json.loads(stored)
    return TerminalRecord(
        request_id=value["requestId"],
        body=value["body"],
        message_ids=value.get("messageIds"),
    )
