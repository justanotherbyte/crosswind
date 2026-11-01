import sys

import pydantic_ai
import pytest


def test_an_older_pydantic_ai_fails_with_the_fix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pydantic_ai, "__version__", "1.60.0")
    monkeypatch.delitem(sys.modules, "agents.harness.pydantic_ai")

    with pytest.raises(ImportError, match=r"found 1\.60\.0.*index\.pyodide\.org/0\.29\.3"):
        import agents.harness.pydantic_ai  # noqa: F401
