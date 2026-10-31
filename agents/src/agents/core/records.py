"""Record fields with a different wire name (``.design/utilities.md`` §5).

SDK records are dataclasses with snake_case attributes; codecs read a field's
wire (JSON) name from its metadata.
"""

from dataclasses import MISSING, field
from typing import Any

__all__ = ("wire",)


def wire(name: str, default: Any = MISSING) -> Any:
    return field(default=default, metadata={"wire": name})
