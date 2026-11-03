from ..core.errors import AgentsException

__all__ = ("ChannelsError", "ResponseGrammarError")


class ChannelsError(AgentsException):
    """Base class for Channels errors."""


class ResponseGrammarError(ChannelsError):
    """A chunk breaks the response grammar. Nothing was recorded."""
