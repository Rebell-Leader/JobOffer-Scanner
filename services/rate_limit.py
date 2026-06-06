"""Token-bucket-ish rate limiting with pluggable backend.

Why this exists:
  * Auth endpoints (login, register, forgot-password) can be brute-forced or
    spammed. We cap attempts per identifier in a rolling window.
  * Analysis is expensive (LLM tokens cost money). We cap analyses per user.

Backend selection:
  * Single-process in-memory (default) — fine for one Streamlit container.
  * Redis (when ``REDIS_URL`` is set) — for multi-worker / multi-container
    deployments. Falls back to in-memory if redis isn't installed.

Identifier note: Streamlit doesn't reliably expose client IP, so for the web
UI we key on email (and, for analysis, on user_id). Behind a real reverse
proxy you can pass an IP-derived key in instead.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Deque, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after: float  # seconds until the next attempt would be allowed (0 if allowed)


class _MemoryBackend:
    """Thread-safe sliding-window counter, keyed by string."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: Dict[str, Deque[float]] = defaultdict(deque)

    def record_and_check(self, key: str, max_events: int, window: float) -> RateLimitDecision:
        now = time.monotonic()
        with self._lock:
            events = self._events[key]
            cutoff = now - window
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= max_events:
                retry_after = max(0.0, events[0] + window - now)
                return RateLimitDecision(allowed=False, retry_after=retry_after)
            events.append(now)
            return RateLimitDecision(allowed=True, retry_after=0.0)

    def reset(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)


class _RedisBackend:
    """Sliding-window counter in Redis using a sorted set per key."""

    def __init__(self, url: str) -> None:
        import redis  # lazy import — only used when REDIS_URL is set

        self._redis = redis.from_url(url, decode_responses=True)

    def record_and_check(self, key: str, max_events: int, window: float) -> RateLimitDecision:
        now = time.time()
        cutoff = now - window
        pipe = self._redis.pipeline()
        pipe.zremrangebyscore(key, 0, cutoff)
        pipe.zcard(key)
        pipe.zadd(key, {f"{now}:{os.getpid()}:{id(pipe)}": now})
        pipe.expire(key, int(window) + 1)
        _, current, _, _ = pipe.execute()
        if current >= max_events:
            # Roll back the add we just made so a denied attempt doesn't count.
            self._redis.zpopmax(key, 1)
            oldest = self._redis.zrange(key, 0, 0, withscores=True)
            retry_after = max(0.0, (oldest[0][1] + window - now)) if oldest else window
            return RateLimitDecision(allowed=False, retry_after=retry_after)
        return RateLimitDecision(allowed=True, retry_after=0.0)

    def reset(self, key: str) -> None:
        self._redis.delete(key)


def _build_backend():
    url = os.getenv("REDIS_URL") or os.getenv("CELERY_BROKER_URL")
    if url and url.startswith(("redis://", "rediss://")):
        try:
            return _RedisBackend(url)
        except Exception as exc:  # noqa: BLE001 - degrade silently to memory
            logger.warning("Redis rate-limit backend unavailable (%s); using in-memory.", exc)
    return _MemoryBackend()


# Singleton backend, lazily built so tests can clear env first.
_backend = None


def _get_backend():
    global _backend
    if _backend is None:
        _backend = _build_backend()
    return _backend


def reset_backend_for_testing() -> None:
    """Wipe the singleton so the next call picks up the current env."""
    global _backend
    _backend = None


class RateLimiter:
    """Public wrapper. Construct once per action; call ``check`` per attempt."""

    def __init__(self, action: str, max_attempts: int, window_seconds: float) -> None:
        self.action = action
        self.max_attempts = max_attempts
        self.window = window_seconds

    def check(self, identifier: str) -> RateLimitDecision:
        if self.max_attempts <= 0:
            return RateLimitDecision(allowed=True, retry_after=0.0)
        key = f"rl:{self.action}:{identifier}"
        return _get_backend().record_and_check(key, self.max_attempts, self.window)

    def reset(self, identifier: str) -> None:
        _get_backend().reset(f"rl:{self.action}:{identifier}")


# Defaults tuned for "hostile but not DoS" — generous enough for forgetful
# users, strict enough that credential-stuffing or runaway costs get blocked.
def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


LOGIN_LIMITER = RateLimiter(
    "login",
    max_attempts=_env_int("RL_LOGIN_MAX", 10),
    window_seconds=_env_int("RL_LOGIN_WINDOW", 300),
)
REGISTER_LIMITER = RateLimiter(
    "register",
    max_attempts=_env_int("RL_REGISTER_MAX", 5),
    window_seconds=_env_int("RL_REGISTER_WINDOW", 3600),
)
RESET_LIMITER = RateLimiter(
    "reset",
    max_attempts=_env_int("RL_RESET_MAX", 5),
    window_seconds=_env_int("RL_RESET_WINDOW", 3600),
)
ANALYSIS_LIMITER = RateLimiter(
    "analysis",
    max_attempts=_env_int("RL_ANALYSIS_MAX", 30),
    window_seconds=_env_int("RL_ANALYSIS_WINDOW", 3600),
)


class RateLimitExceeded(Exception):
    """Raised when an action is denied. Carries ``retry_after`` seconds."""

    def __init__(self, retry_after: float):
        self.retry_after = retry_after
        super().__init__(self._format())

    def _format(self) -> str:
        if self.retry_after >= 60:
            return f"Too many attempts. Try again in {int(self.retry_after // 60)} min."
        return f"Too many attempts. Try again in {int(self.retry_after) + 1} s."
