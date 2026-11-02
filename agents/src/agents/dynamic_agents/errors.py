from ..core.errors import AgentsException

__all__ = (
    "InvalidSubAgentNameError",
    "SubAgentAbortedError",
    "UnknownSubAgentClassError",
)


class SubAgentAbortedError(AgentsException):
    """A sub-agent was aborted (``dynamic_agents.abort``) while a call was pending.

    The default reason ``abort`` raises in pending callers.
    """


class InvalidSubAgentNameError(ValueError):
    pass


class UnknownSubAgentClassError(ValueError):
    pass
