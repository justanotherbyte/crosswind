from dataclasses import dataclass
from datetime import date, datetime

from .errors import WyndError
from .wrangler_config import WranglerConfig

__all__ = ("Target", "select_target")


@dataclass(frozen=True, kw_only=True)
class Target:
    version: str
    flag: str
    since: date | None
    interpreter: str
    index: str

    def matches(self, flags: tuple[str, ...], day: date) -> bool:
        """Whether a config with these flags and date runs this Python."""
        return self.flag in flags or (self.since is not None and day >= self.since)


# select_target returns the first match, so the newest Python comes first.
TARGETS = (
    Target(
        version="3.14",
        flag="python_workers_314",
        since=date(2026, 9, 8),
        interpreter="cpython-3.14.2-emscripten-wasm32-musl",
        index="https://index.pyodide.org/314.0.7",
    ),
    Target(
        version="3.13",
        flag="python_workers_20250116",
        since=date(2025, 9, 29),
        interpreter="cpython-3.13.2-emscripten-wasm32-musl",
        index="https://index.pyodide.org/0.28.3",
    ),
    Target(
        version="3.12",
        flag="python_workers",
        since=None,
        interpreter="cpython-3.12.7-emscripten-wasm32-musl",
        index="https://index.pyodide.org/0.27.7",
    ),
)
LATEST_TARGET = TARGETS[0]

def select_target(config: WranglerConfig) -> Target:
    flags = config.compatibility_flags
    found = (
        f"{config.source.name} has compatibility_date "
        f"{config.compatibility_date!r} and compatibility_flags {list(flags)}"
    )

    if config.compatibility_date is None:
        raise WyndError(f"{found}. Set a compatibility_date.")
    if "python_workers" not in flags:
        raise WyndError(f"{found}. Add python_workers to compatibility_flags.")
    try:
        day = datetime.strptime(config.compatibility_date, "%Y-%m-%d").date()
    except ValueError:
        raise WyndError(f"{found}. Write the date as YYYY-MM-DD.") from None

    for target in TARGETS:
        if target.matches(flags, day):
            return target
    raise AssertionError("the 3.12 row matches every python_workers config")
