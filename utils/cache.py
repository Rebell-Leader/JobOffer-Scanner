"""Thread-safe in-memory cache with TTL.

Streamlit reruns can execute concurrently across sessions, so the previous
plain-dict cache could race. This is still an in-process cache — Phase 3
swaps it for Redis.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Any, Optional


class SimpleCache:
    def __init__(self, ttl_seconds: int = 3600):
        self._cache: dict[str, tuple[Any, datetime]] = {}
        self._ttl = timedelta(seconds=ttl_seconds)
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            value, ts = entry
            if datetime.now() - ts >= self._ttl:
                del self._cache[key]
                return None
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._cache[key] = (value, datetime.now())

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


cache = SimpleCache()
