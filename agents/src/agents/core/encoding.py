"""Design: ``.design/agent_api.md`` §1.8.

Datetimes encode as epoch milliseconds because upstream's records use them.
"""

import dataclasses
import json
from datetime import datetime
from typing import Any

from .timing import epoch_ms

__all__ = ("byte_length", "to_js_json", "to_json")


def to_json(value: Any) -> str:
    # allow_nan=False: JSON.parse can't read NaN or infinities back.
    return json.dumps(value, default=_encode, separators=(",", ":"), allow_nan=False)


def to_js_json(value: Any) -> str:
    # Matches JSON.stringify, so stored sizes and byte budgets match upstream's.
    # Escaping non-ASCII would store 2-3x the bytes.
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def byte_length(text: str) -> int:
    # A lone surrogate (valid in a JS string) counts 3 bytes instead of raising.
    return len(text.encode("utf-8", "surrogatepass"))


def _encode(value: Any) -> Any:
    if isinstance(value, datetime):
        return epoch_ms(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _wire_fields(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _wire_fields(record: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field in dataclasses.fields(record):
        value = getattr(record, field.name)
        wire_name = field.metadata.get("wire")
        if wire_name is None:
            out[field.name] = value
        # The wire format omits an optional field that is None instead of sending null.
        elif value is not None or field.default is not None:
            out[wire_name] = value
    return out
