"""A harness that runs a Pydantic AI agent.

Needs Pydantic AI 2.64 or later: ``cf-agents[pydantic-ai]``. It runs on
Python 3.13 and 3.14 Workers. On Python 3.13 Workers, pywrangler's default
package index stops at pydantic 2.10, so add a newer index with the same ABI
to the Worker's ``pyproject.toml``::

    [tool.uv.pip]
    extra-index-url = ["https://index.pyodide.org/0.29.3"]

``agents.harness`` never imports this package.
"""

from __future__ import annotations

try:
    import pydantic_ai
except ModuleNotFoundError as error:
    raise ImportError(
        "agents.harness.pydantic_ai needs Pydantic AI: install cf-agents[pydantic-ai]"
    ) from error

if tuple(int(part) for part in pydantic_ai.__version__.split(".")[:2]) < (2, 64):
    raise ImportError(
        f"agents.harness.pydantic_ai needs pydantic-ai-slim>=2.64, found "
        f"{pydantic_ai.__version__}. On Python 3.13 Workers, add "
        f'extra-index-url = ["https://index.pyodide.org/0.29.3"] under '
        f"[tool.uv.pip] in pyproject.toml and run pywrangler sync again"
    )

from .harness import PydanticAIHarness
from .workers_ai import WorkersAIModel

__all__ = ("PydanticAIHarness", "WorkersAIModel")
