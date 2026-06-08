"""Typed environment-variable accessors + a record of what was actually read.

One place for env-parsing semantics so:

  * Defaults aren't duplicated per call site (and can't silently diverge).
  * Boolean parsing isn't the fragile ``== "1"`` check — ``true`` / ``yes`` /
    ``on`` (any case) now work too, and an unrecognised value warns instead of
    silently reading as false.
  * Every read is recorded, so :func:`effective_config` /
    ``utils.config.log_effective_config`` can show the operator the resolved,
    NON-default configuration at startup — turning a typo'd var (which would
    otherwise silently fall back to its default) into something visible.

Values are read live from ``os.environ`` on every call (never cached), so tests
that flip a var with ``mock.patch.dict`` still take effect. The recording is a
best-effort side-table; it only reflects vars read so far (module-level reads
happen at import, function-level reads on first call).
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}

_lock = threading.Lock()
# name -> (resolved_value, default, overridden_by_env)
_seen: Dict[str, Tuple[object, object, bool]] = {}


def _record(name: str, value: object, default: object, overridden: bool) -> None:
    with _lock:
        _seen[name] = (value, default, overridden)


def env_str(name: str, default: Optional[str] = None) -> Optional[str]:
    raw = os.getenv(name)
    value = raw if raw is not None else default
    _record(name, value, default, raw is not None)
    return value


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        _record(name, default, default, False)
        return default
    s = raw.strip().lower()
    if s in _TRUE:
        value = True
    elif s in _FALSE:
        value = False
    else:
        logger.warning(
            "env %s=%r is not a recognised boolean (use 1/0/true/false); using %s.",
            name, raw, default,
        )
        value = default
    _record(name, value, default, True)
    return value


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        _record(name, default, default, False)
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("env %s=%r is not an int; using %s.", name, raw, default)
        value = default
    _record(name, value, default, value != default or raw.strip() != str(default))
    return value


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        _record(name, default, default, False)
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("env %s=%r is not a number; using %s.", name, raw, default)
        value = default
    _record(name, value, default, True)
    return value


def effective_config(include_defaults: bool = False) -> Dict[str, object]:
    """Return the env reads recorded so far.

    By default only the vars whose value was overridden by the environment
    (i.e. differ from the in-code default in origin) are returned.
    """
    with _lock:
        return {
            name: value
            for name, (value, _default, overridden) in sorted(_seen.items())
            if include_defaults or overridden
        }


def reset_for_testing() -> None:
    with _lock:
        _seen.clear()
