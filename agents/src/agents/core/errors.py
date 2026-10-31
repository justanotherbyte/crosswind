"""Misuse raises built-in exceptions, not `AgentsException` subclasses.

A wrong argument type or value, or a call at the wrong time, raises
``TypeError`` / ``ValueError`` / ``RuntimeError`` (``.design/utilities.md`` §1).
"""

__all__ = ("AgentsException", "SqlError")


class AgentsException(Exception):  # noqa: N818  (the decided public name)
    """Base class for every exception the SDK raises on purpose."""


class SqlError(AgentsException):
    """A SQL statement failed.

    Port of upstream ``sql-error.ts``.

    Parameters
    ----------
    query
        The statement that failed.
    message
        SQLite's error message.
    """

    def __init__(self, query: str, message: str) -> None:
        super().__init__(f"SQL query failed: {message}")
        self.query = query
