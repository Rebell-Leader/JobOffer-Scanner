"""TTL cache with a pluggable backend.

Default is a thread-safe in-memory cache (fine for a single process). When
``REDIS_URL`` (or ``CELERY_BROKER_URL``) points at Redis AND the ``redis``
package is installed, a Redis-backed cache is used instead so multiple app
instances share cached LLM/data-source results — required for horizontal
scaling (Streamlit reruns + multiple replicas).

Cached values MUST be JSON-serialisable (today: parsed dicts and report
strings). The Redis backend JSON-encodes on set and decodes on get; an
unserialisable value is silently skipped rather than raised, so a caching
path can never break the request that produced the value.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DEFAULT_TTL = int(os.getenv("CACHE_TTL_SECONDS", "3600"))
_NAMESPACE = "joc:cache:"


class SimpleCache:
    """Thread-safe in-process TTL cache."""

    def __init__(self, ttl_seconds: int = _DEFAULT_TTL):
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


class RedisCache:
    """Redis-backed TTL cache. JSON-serialises values under a namespace."""

    def __init__(self, url: str, ttl_seconds: int = _DEFAULT_TTL):
        import redis  # lazy — only when configured

        self._r = redis.from_url(url, decode_responses=True)
        self._ttl = ttl_seconds

    def _k(self, key: str) -> str:
        return f"{_NAMESPACE}{key}"

    def get(self, key: str) -> Optional[Any]:
        try:
            raw = self._r.get(self._k(key))
        except Exception as exc:  # noqa: BLE001 - cache must never break callers
            logger.warning("RedisCache get failed: %s", exc)
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return None

    def set(self, key: str, value: Any) -> None:
        try:
            payload = json.dumps(value, default=str)
        except (TypeError, ValueError):
            return  # uncacheable value — skip, don't raise
        try:
            self._r.set(self._k(key), payload, ex=self._ttl)
        except Exception as exc:  # noqa: BLE001
            logger.warning("RedisCache set failed: %s", exc)

    def clear(self) -> None:
        try:
            for k in self._r.scan_iter(f"{_NAMESPACE}*"):
                self._r.delete(k)
        except Exception as exc:  # noqa: BLE001
            logger.warning("RedisCache clear failed: %s", exc)


def _redis_url() -> Optional[str]:
    url = os.getenv("REDIS_URL") or os.getenv("CELERY_BROKER_URL")
    if url and url.startswith(("redis://", "rediss://")):
        return url
    return None


def build_cache():
    """Pick the cache backend from the environment."""
    url = _redis_url()
    if url:
        try:
            backend = RedisCache(url)
            logger.info("Using Redis cache backend.")
            return backend
        except Exception as exc:  # noqa: BLE001 - degrade to in-memory
            logger.warning("Redis cache unavailable (%s); using in-memory.", exc)
    return SimpleCache()


# Module-level singleton used across tools/*. Built once at import from env.
cache = build_cache()
