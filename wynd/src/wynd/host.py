import re

from .console import warn
from .project import Project
from .targets import Target
from .utils import read_text

__all__ = ("warn_on_venv_mismatch",)


def warn_on_venv_mismatch(project: Project, target: Target) -> None:
    """Warn when the editor's .venv runs another Python minor than the target."""
    pyvenv = read_text(project.root / ".venv" / "pyvenv.cfg") or ""
    match = re.search(r"^version_info\s*=\s*(\d+\.\d+)", pyvenv, re.MULTILINE)
    if match is None or match.group(1) == target.version:
        return

    config = project.config
    flags = ", ".join(f for f in config.compatibility_flags if "python_workers" in f)
    warn(
        f".venv is Python {match.group(1)} but {config.source.name}"
        f" targets {target.version} ({flags}, {config.compatibility_date}).\n"
        "Editor types may differ from production."
        f" To match: uv venv --python {target.version} && uv sync"
    )
