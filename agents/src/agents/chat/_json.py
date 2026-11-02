import json
from typing import Any

__all__ = ("dumps", "stable_dumps")


# Matches upstream's ``JSON.stringify`` output, so chat's comparisons agree with it.
def dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


# Upstream's ``stableStringify``.
def stable_dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, sort_keys=True)
