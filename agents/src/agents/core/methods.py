"""Looking up methods by name, and names by method.

Capabilities store callbacks by name (schedules, queue items) and resolve
them again when a job fires, possibly after the object was evicted and
rebuilt (``.design/utilities.md`` §2).
"""

import inspect
from collections.abc import Callable, Mapping
from typing import Any

__all__ = ("get_bound_method", "method_name", "resolve_callback")


def get_bound_method(obj: object, name: str) -> Callable[..., Any]:
    """Return ``obj``'s bound method called ``name``.

    Parameters
    ----------
    obj
        The object to look the method up on.
    name
        The method's attribute name.

    Returns
    -------
    Callable[..., Any]
        The bound method.

    Raises
    ------
    AttributeError
        If ``obj`` has no attribute ``name``.
    TypeError
        If the attribute isn't a method bound to ``obj`` (a plain function or
        callable stored on it, a ``classmethod``, or a ``staticmethod``).
    """
    method = getattr(obj, name)
    if not inspect.ismethod(method) or method.__self__ is not obj:
        raise TypeError(f"{type(obj).__name__}.{name} is not a method")
    return method


def method_name(obj: object, callback: str | Callable[..., Any]) -> str:
    """Return the name to store for ``callback``, checking it resolves on ``obj``.

    Parameters
    ----------
    obj
        The object the callback will be looked up on later.
    callback
        A method name, or a bound method of ``obj``.

    Returns
    -------
    str
        The method's name.

    Raises
    ------
    AttributeError
        If the name doesn't resolve on ``obj``.
    TypeError
        If the name resolves to something that isn't a method of ``obj``.
    ValueError
        If ``callback`` is a callable but not a bound method of ``obj``
        (including callables without a name, such as a ``functools.partial``).
    """
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
    """Return the callback stored as ``name``, or ``None`` if nothing matches.

    ``callbacks`` wins over a method of ``target`` with the same name.
    """
    handler = callbacks.get(name)
    if handler is not None or target is None:
        return handler
    try:
        return get_bound_method(target, name)
    except (AttributeError, TypeError):
        return None
