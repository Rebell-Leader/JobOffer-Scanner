"""Per-run pipeline checkpointing for resumable analysis.

When an LLM call fails mid-pipeline (e.g., the salary stage 502s after the job
+ company stages already succeeded), the user shouldn't have to pay for the
re-runs of the stages that already completed. This module provides a small,
thread-safe checkpoint store that the orchestrator writes to after each stage
and reads from on resume.

Default backend is in-memory and process-local. The checkpoint *key* is a
deterministic hash of the inputs (posting + manual inputs + model + resume
text), so:

  * Re-submitting the same form re-uses the partial work automatically.
  * Editing any input invalidates the checkpoint (new key, fresh run).
  * Two users analyzing the same posting don't collide because user_id is
    folded into the key.

The store deliberately exposes only the three operations the orchestrator
needs (``get`` / ``set`` / ``clear``) so swapping in a Redis backend later is
a one-class change.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from utils.env import env_int

logger = logging.getLogger(__name__)


# What stages of the orchestrator write checkpointable output. Order here
# mirrors the LangGraph edges in agents/orchestrator.py and is the order the
# UI uses to describe progress on resume.
CHECKPOINT_STAGES: Tuple[str, ...] = (
    "job_details",
    "company_analysis",
    "salary_analysis",
    "resume_analysis",   # only set when a resume was uploaded
    "verdict_and_report",
)


@dataclass
class CheckpointPayload:
    """A snapshot of the orchestrator state for one checkpoint key."""

    # Each entry maps a stage name -> the dict the orchestrator merges back
    # into its working state.
    stages: Dict[str, Any] = field(default_factory=dict)

    def has(self, stage: str) -> bool:
        return stage in self.stages

    def get(self, stage: str) -> Any:
        return self.stages.get(stage)

    def set(self, stage: str, value: Any) -> None:
        self.stages[stage] = value

    def completed_stages(self) -> Tuple[str, ...]:
        return tuple(s for s in CHECKPOINT_STAGES if s in self.stages)


class CheckpointStore:
    """Thread-safe process-local checkpoint store."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: Dict[str, CheckpointPayload] = {}

    def get(self, key: str) -> CheckpointPayload:
        """Return the payload for ``key``, creating an empty one if missing."""
        with self._lock:
            payload = self._data.get(key)
            if payload is None:
                payload = CheckpointPayload()
                self._data[key] = payload
            return payload

    def has(self, key: str) -> bool:
        with self._lock:
            payload = self._data.get(key)
            return bool(payload and payload.stages)

    def set(self, key: str, stage: str, value: Any) -> None:
        with self._lock:
            payload = self._data.setdefault(key, CheckpointPayload())
            payload.set(stage, value)

    def completed_stages(self, key: str) -> Tuple[str, ...]:
        with self._lock:
            payload = self._data.get(key)
            return payload.completed_stages() if payload else ()

    def clear(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def reset(self) -> None:
        """Wipe the whole store. Used by tests."""
        with self._lock:
            self._data.clear()


class RedisCheckpointStore:
    """Redis-backed checkpoint store so a retry can land on a different replica
    and still resume completed stages. Each key is a Redis hash (stage ->
    JSON) with a TTL so abandoned checkpoints self-expire.
    """

    _NS = "joc:ckpt:"
    _TTL = env_int("CHECKPOINT_TTL_SECONDS", 3600)

    def __init__(self, url: str) -> None:
        import redis  # lazy

        self._r = redis.from_url(url, decode_responses=True)

    def _k(self, key: str) -> str:
        return f"{self._NS}{key}"

    def get(self, key: str) -> CheckpointPayload:
        payload = CheckpointPayload()
        try:
            raw = self._r.hgetall(self._k(key))
        except Exception as exc:  # noqa: BLE001
            logger.warning("RedisCheckpointStore get failed: %s", exc)
            return payload
        for stage, val in (raw or {}).items():
            try:
                payload.stages[stage] = json.loads(val)
            except (TypeError, ValueError):
                continue
        return payload

    def has(self, key: str) -> bool:
        try:
            return bool(self._r.exists(self._k(key)))
        except Exception as exc:  # noqa: BLE001
            logger.warning("RedisCheckpointStore has failed: %s", exc)
            return False

    def set(self, key: str, stage: str, value: Any) -> None:
        try:
            self._r.hset(self._k(key), stage, json.dumps(value, default=str))
            self._r.expire(self._k(key), self._TTL)
        except (TypeError, ValueError):
            return  # unserialisable — skip
        except Exception as exc:  # noqa: BLE001
            logger.warning("RedisCheckpointStore set failed: %s", exc)

    def completed_stages(self, key: str) -> Tuple[str, ...]:
        try:
            present = set(self._r.hkeys(self._k(key)) or [])
        except Exception as exc:  # noqa: BLE001
            logger.warning("RedisCheckpointStore completed_stages failed: %s", exc)
            return ()
        return tuple(s for s in CHECKPOINT_STAGES if s in present)

    def clear(self, key: str) -> None:
        try:
            self._r.delete(self._k(key))
        except Exception as exc:  # noqa: BLE001
            logger.warning("RedisCheckpointStore clear failed: %s", exc)

    def reset(self) -> None:
        try:
            for k in self._r.scan_iter(f"{self._NS}*"):
                self._r.delete(k)
        except Exception as exc:  # noqa: BLE001
            logger.warning("RedisCheckpointStore reset failed: %s", exc)


def _build_store():
    url = os.getenv("REDIS_URL") or os.getenv("CELERY_BROKER_URL")
    if url and url.startswith(("redis://", "rediss://")):
        try:
            store = RedisCheckpointStore(url)
            logger.info("Using Redis checkpoint store.")
            return store
        except Exception as exc:  # noqa: BLE001 - degrade to in-memory
            logger.warning("Redis checkpoint store unavailable (%s); in-memory.", exc)
    return CheckpointStore()


# Module-level singleton — built once at import from the environment.
_store = _build_store()


def get_store():
    return _store


def reset_store_for_testing() -> None:
    """Reset to a fresh in-memory store (tests never use Redis)."""
    global _store
    _store = CheckpointStore()


def compute_key(
    job_posting: str,
    manual_inputs: Optional[dict],
    model: Optional[str],
    resume_text: Optional[str],
    user_id: Optional[int] = None,
) -> str:
    """Deterministic content-derived key.

    Folds the user_id in so concurrent users analyzing the same posting can't
    accidentally share partial state. SHA-256 truncated to 16 hex chars is
    plenty — the key space is per-process, not global.
    """
    payload = {
        "user": user_id,
        "posting": (job_posting or "").strip(),
        "manual": _normalize_inputs(manual_inputs or {}),
        "model": (model or "").strip().lower(),
        "resume": (resume_text or "").strip(),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def _normalize_inputs(d: dict) -> dict:
    """Strip None / empty values so cosmetic form differences don't change the
    key — e.g., user retyping the same company name shouldn't bust the cache.
    """
    return {
        k: (v.strip() if isinstance(v, str) else v)
        for k, v in sorted(d.items())
        if v not in (None, "", [])
    }
