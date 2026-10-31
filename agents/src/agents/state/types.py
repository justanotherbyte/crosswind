from collections.abc import Callable
from typing import Literal

from ..lifecycle.types import Connection

__all__ = ("StateChangeHandler", "StateSource", "StateValidator")

type StateSource = Connection | Literal["server"]
"""Where a change came from: ``"server"`` for host code, or the connection a
client's change arrived on."""

# Raises to reject the change.
type StateValidator[T] = Callable[[T, StateSource], None]

type StateChangeHandler[T] = Callable[[T, StateSource], None]
