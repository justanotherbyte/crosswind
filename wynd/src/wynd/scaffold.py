import re
from importlib.metadata import version
from pathlib import Path

from .console import info
from .errors import WyndError
from .project import load_project
from .sources import PythonConfigParser, WranglerConfigParser
from .targets import LATEST_TARGET
from .typegen import write_env
from .utils import read_text

__all__ = ("scaffold",)

# Wynd is a dev dependency, so editors resolve wynd.config. Sync compiles only
# [project] dependencies, so it never reaches the Worker.
PYPROJECT = """\
[project]
name = "{name}"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [{dependencies}]

[dependency-groups]
dev = ["wynd>={wynd}"]
"""

CONFIG = """\
from wynd.config import Config, Worker

config = Config(
    worker=Worker(
        name="{name}",
        entrypoint="src/main.py",
        compatibility_date="{date}",
    ),
)
"""

MAIN = """\
from env import Env
from workers import Response, WorkerEntrypoint


class Default(WorkerEntrypoint[Env]):
    async def fetch(self, request):
        return Response("Hello from Python!")
"""

# workers-runtime-sdk has no workers.bindings, so there is no generated Env.
STOCK_MAIN = """\
from workers import Response, WorkerEntrypoint


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return Response("Hello from Python!")
"""

# The add-on SDKs `wynd init` offers, by their distribution names.
ADDONS = {"containers": "containers-py", "agents": "cf-agents"}

IGNORED = (".venv/", ".wynd/")


def scaffold(
    dir: Path,
    sdk: str = "crosswind-workers",
    additional_sdks: tuple[str, ...] = (),
) -> None:
    """Write a new project on `sdk`, plus the `additional_sdks` from ADDONS,
    into `dir`."""
    name = worker_name(dir)
    files = project_files(name, sdk, additional_sdks)
    refuse_existing_project(dir, files)
    write_files(dir, files)
    ignore(dir / ".gitignore")
    if sdk == "crosswind-workers":
        write_env(load_project(dir, None, None))
    info(f"created {name}. Run `wynd dev` in {dir} to serve it.")


def project_files(
    name: str, sdk: str, additional_sdks: tuple[str, ...]
) -> dict[str, str]:
    """The text of each file a new project starts with, by path."""
    distributions = [sdk] + [ADDONS[addon] for addon in additional_sdks]
    dependencies = ", ".join(f'"{dist}"' for dist in distributions)
    return {
        "pyproject.toml": PYPROJECT.format(
            name=name, dependencies=dependencies, wynd=version("wynd")
        ),
        # uv sync builds the editor's .venv on the target's Python.
        ".python-version": f"{LATEST_TARGET.version}\n",
        # The newest target's date, because the pinned Wrangler rejects later ones.
        PythonConfigParser.names[0]: CONFIG.format(
            name=name, date=LATEST_TARGET.since
        ),
        "src/main.py": MAIN if sdk == "crosswind-workers" else STOCK_MAIN,
    }


def refuse_existing_project(directory: Path, files: dict[str, str]) -> None:
    """Raise WyndError if `directory` has a file that wynd init writes or
    generates."""
    paths = [*files, *WranglerConfigParser.names, "src/env.py"]
    existing = [path for path in paths if (directory / path).exists()]
    if existing:
        raise WyndError(
            f"{directory} already has {', '.join(existing)}. "
            "wynd init only writes a new project."
        )


def write_files(directory: Path, files: dict[str, str]) -> None:
    for path, text in files.items():
        (directory / path).parent.mkdir(parents=True, exist_ok=True)
        (directory / path).write_text(text)


def worker_name(directory: Path) -> str:
    """The directory's name as lowercase words joined by hyphens, so
    `My App_2` becomes `my-app-2`."""
    words = re.findall(r"[a-z0-9]+", directory.resolve().name.lower())
    return "-".join(words) or "worker"


def ignore(path: Path) -> None:
    """Add the ignore lines a .gitignore lacks, keeping what it has."""
    text = read_text(path) or ""
    missing = [line for line in IGNORED if line not in text.splitlines()]
    if not missing:
        return
    if text and not text.endswith("\n"):
        text += "\n"
    path.write_text(text + "".join(f"{line}\n" for line in missing))
