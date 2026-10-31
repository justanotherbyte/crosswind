"""Callbacks are stored by name (``.design/utilities.md`` §2).

Schedules and queue items resolve the name again when a job fires, possibly
after the object was evicted and rebuilt.
"""

import inspect
from collections.abc import Callable, Mapping
from typing import Any

__all__ = ("get_bound_method", "method_name", "resolve_callback")


def get_bound_method(obj: object, name: str) -> Callable[..., Any]:
    method = getattr(obj, name)
    if not inspect.ismethod(method) or method.__self__ is not obj:
        raise TypeError(f"{type(obj).__name__}.{name} is not a method")
    return method


def method_name(obj: object, callback: str | Callable[..., Any]) -> str:
    name = (
        callback if isinstance(callback, str) else getattr(callback, "__name__", None)
    )
    if not isinstance(name, str):
        raise ValueError(f"{callback!r} is not a method; pass a bound method or a name")

    method = get_bound_method(obj, name)
    # Bound methods are created on each access, so compare with ==, not `is`.
    if not isinstance(callback, str) and method != callback:
        raise ValueError(f"{callback!r} is not a method of {type(obj).__name__}")
    return name


def resolve_callback(
    callbacks: Mapping[str, Callable[..., Any]], target: object | None, name: str
) -> Callable[..., Any] | None:
    handler = callbacks.get(name)
    if handler is not None or target is None:
        return handler
    try:
        return get_bound_method(target, name)
    except (AttributeError, TypeError):
        return None
