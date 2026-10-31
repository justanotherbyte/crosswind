from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, TypedDict

from ..core.types import JSONValue, RetryOptions

__all__ = (
    "ByIdMessage",
    "CancelAllMessage",
    "DispatchMessage",
    "ListMessage",
    "PushMessage",
    "QueueCallback",
    "QueueErrorHandler",
    "QueueItem",
    "QueueJobPayload",
    "QueueRouteMessage",
    "WireQueueItem",
)


@dataclass(slots=True, kw_only=True)
class QueueItem:
    """One durable item waiting in a Queue.

    Parameters
    ----------
    id
        The item's id.
    callback
        The name of the callback the item runs.
    payload
        The data passed to the callback.
    created_at
        When the item was first pushed.
    retry
        The retry policy for the callback.
    """

    id: str
    callback: str
    payload: JSONValue
    created_at: datetime
    retry: RetryOptions | None = None


type QueueCallback = Callable[[Any, QueueItem], Awaitable[Any]]
type QueueErrorHandler = Callable[[Exception], Awaitable[None]]


class QueueJobPayload(TypedDict):
    payload: JSONValue
    # The owning facet's route address, or None for items pushed on the root.
    owner_path: str | None
    owner_path_key: str | None


class WireQueueItem(TypedDict):
    id: str
    callback: str
    payload: JSONValue
    created_at_ms: int
    retry: str | None


class PushMessage(TypedDict):
    type: Literal["push"]
    callback: str
    payload: JSONValue
    id: str | None
    retry: str | None


class ByIdMessage(TypedDict):
    type: Literal["get", "cancel"]
    id: str


class ListMessage(TypedDict):
    type: Literal["list"]
    callback: str | None


class CancelAllMessage(TypedDict):
    type: Literal["cancel_all"]
    callback: str | None


class DispatchMessage(TypedDict):
    type: Literal["dispatch"]
    item: WireQueueItem


type QueueRouteMessage = (
    PushMessage | ByIdMessage | ListMessage | CancelAllMessage | DispatchMessage
)
