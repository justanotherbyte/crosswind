import tomllib
from pathlib import Path
from typing import Any

import jsonc

from .errors import WyndError

__all__ = ("find_up", "read", "read_text")


def find_up(start: Path, name: str) -> Path | None:
    """The nearest of `start` and its parents that holds a file `name`."""
    for directory in (start, *start.parents):
        if (directory / name).is_file():
            return directory
    return None


def read(path: Path) -> dict[str, Any]:
    """A TOML, JSON or JSONC file's table. WyndError when it can't be read."""
    try:
        text = path.read_text()
        if path.suffix == ".toml":
            return tomllib.loads(text)
        return jsonc.loads(text)
    except OSError as error:
        raise WyndError(f"cannot read {path}: {error.strerror}") from None
    except (tomllib.TOMLDecodeError, ValueError) as error:
        raise WyndError(f"cannot parse {path}: {error}") from None


def read_text(path: Path) -> str | None:
    """A file's text, or None when there is no file."""
    try:
        return path.read_text()
    except FileNotFoundError:
        return None
