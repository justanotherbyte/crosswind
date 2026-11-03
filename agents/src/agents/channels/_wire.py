"""Records to wire form, per ``.design/utilities.md`` §5."""

import dataclasses
from functools import cache
from typing import Any, NamedTuple

from ..core.types import JSONValue

__all__ = ("record_to_wire",)

# Class constants that discriminate a union on the wire.
_TAGS = ("type", "status", "ok")


class _Field(NamedTuple):
    name: str
    wire: str
    optional: bool


@cache
def _fields(cls: Any) -> tuple[_Field, ...]:
    return tuple(
        _Field(
            name=f.name,
            wire=f.metadata.get("wire", f.name),
            optional=f.default is None,
        )
        for f in dataclasses.fields(cls)
    )


def record_to_wire(record: Any) -> dict[str, JSONValue]:
    """Return a record's wire form, leaving out optional fields that are ``None``."""
    cls = type(record)
    out: dict[str, JSONValue] = {
        tag: getattr(cls, tag) for tag in _TAGS if hasattr(cls, tag)
    }
    for f in _fields(cls):
        value = getattr(record, f.name)
        if f.optional and value is None:
            continue
        out[f.wire] = _value_to_wire(value)
    return out


def _value_to_wire(value: Any) -> JSONValue:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return record_to_wire(value)
    if isinstance(value, list):
        return [_value_to_wire(item) for item in value]
    return value
