from ..core.errors import AgentsException
from ..harness.errors import ResponseGrammarError

__all__ = ("ChannelsError", "ResponseGrammarError")


class ChannelsError(AgentsException):
    """Base class for Channels errors."""
