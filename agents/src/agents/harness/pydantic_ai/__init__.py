"""A harness that runs a Pydantic AI agent.

Needs Pydantic AI: ``cf-agents[pydantic-ai]``. ``agents.harness`` never
imports this package.
"""

from __future__ import annotations

try:
    import pydantic_ai  # noqa: F401
except ModuleNotFoundError as error:
    raise ImportError(
        "agents.harness.pydantic_ai needs Pydantic AI: install cf-agents[pydantic-ai]"
    ) from error

from .harness import PydanticAIHarness
from .workers_ai import WorkersAIModel

__all__ = ("PydanticAIHarness", "WorkersAIModel")
