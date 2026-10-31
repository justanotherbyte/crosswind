"""Inputs, step results, metadata, and results are stored as JSON in SQLite.

``None`` is stored as SQL ``NULL``: "no value" (``.design/tasks_engine.md``
§2.2 items 3-5).
"""

import json
import re
from typing import Any

from ..core.encoding import to_js_json
from .errors import TaskSerializationError

__all__ = ("MAX_SERIALIZED_BYTES", "deserialize_task_value", "serialize_task_value")

MAX_SERIALIZED_BYTES = 1_048_576

_SURROGATE = re.compile("[\ud800-\udfff]")


def serialize_task_value(value: Any, error_label: str) -> str | None:
    if value is None:
        return None
    try:
        text = to_js_json(value)
    except (TypeError, ValueError) as error:
        raise TaskSerializationError(error_label, str(error)) from error
    # Escape surrogates as JS's JSON.stringify does, so the text is valid UTF-8.
    text = _SURROGATE.sub(lambda m: f"\\u{ord(m[0]):04x}", text)
    size = len(text.encode())
    if size > MAX_SERIALIZED_BYTES:
        raise TaskSerializationError(
            error_label, f"{size} bytes exceeds the {MAX_SERIALIZED_BYTES}-byte limit"
        )
    return text


def deserialize_task_value(stored: str | None) -> Any:
    return json.loads(stored) if stored is not None else None
