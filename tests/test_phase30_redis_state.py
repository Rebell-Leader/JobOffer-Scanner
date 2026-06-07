"""P0.1: Redis-backed cache + checkpoint store (for horizontal scaling).

Redis isn't available in CI/sandbox, so we inject a small in-memory fake
``redis`` module to exercise the Redis backends, and assert the factory falls
back to the in-process backend when Redis is absent/unconfigured.
"""

from __future__ import annotations

import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


# ---------------------------------------------------------------------------
# Minimal fake redis client (decode_responses=True semantics: str in/out)
# ---------------------------------------------------------------------------

class _FakeRedisClient:
    def __init__(self):
        self.kv: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}

    # string ops
    def get(self, k):
        return self.kv.get(k)

    def set(self, k, v, ex=None):
        self.kv[k] = v

    def delete(self, k):
        self.kv.pop(k, None)
        self.hashes.pop(k, None)

    def scan_iter(self, pattern):
        prefix = pattern.rstrip("*")
        yield from [k for k in list(self.kv) + list(self.hashes) if k.startswith(prefix)]

    # hash ops
    def hgetall(self, k):
        return dict(self.hashes.get(k, {}))

    def hset(self, k, field, value):
        self.hashes.setdefault(k, {})[field] = value

    def hkeys(self, k):
        return list(self.hashes.get(k, {}).keys())

    def expire(self, k, ttl):
        pass

    def exists(self, k):
        return 1 if (k in self.kv or k in self.hashes) else 0


def _install_fake_redis():
    """Put a fake ``redis`` module on sys.modules; return the shared client."""
    client = _FakeRedisClient()
    fake = types.ModuleType("redis")
    fake.from_url = lambda url, decode_responses=True: client  # noqa: ARG005
    return fake, client


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

class CacheFactoryTests(unittest.TestCase):
    def setUp(self):
        for k in ("REDIS_URL", "CELERY_BROKER_URL"):
            os.environ.pop(k, None)

    def test_defaults_to_in_memory(self):
        from utils.cache import SimpleCache, build_cache

        self.assertIsInstance(build_cache(), SimpleCache)

    def test_uses_redis_when_configured(self):
        import importlib
        cache_mod = importlib.import_module('utils.cache')

        fake, _client = _install_fake_redis()
        with mock.patch.dict(os.environ, {"REDIS_URL": "redis://localhost:6379/0"}), \
             mock.patch.dict(sys.modules, {"redis": fake}):
            backend = cache_mod.build_cache()
        self.assertIsInstance(backend, cache_mod.RedisCache)

    def test_falls_back_when_redis_import_fails(self):
        import importlib
        cache_mod = importlib.import_module('utils.cache')

        # URL set but no redis module importable -> in-memory.
        with mock.patch.dict(os.environ, {"REDIS_URL": "redis://x:6379/0"}), \
             mock.patch.dict(sys.modules, {"redis": None}):
            backend = cache_mod.build_cache()
        self.assertIsInstance(backend, cache_mod.SimpleCache)

    def test_ignores_non_redis_url(self):
        import importlib
        cache_mod = importlib.import_module('utils.cache')

        with mock.patch.dict(os.environ, {"CELERY_BROKER_URL": "amqp://x"}):
            self.assertIsInstance(cache_mod.build_cache(), cache_mod.SimpleCache)


class RedisCacheTests(unittest.TestCase):
    def _backend(self):
        import importlib
        cache_mod = importlib.import_module('utils.cache')
        fake, client = _install_fake_redis()
        with mock.patch.dict(sys.modules, {"redis": fake}):
            return cache_mod.RedisCache("redis://localhost:6379/0"), client

    def test_set_get_roundtrip_dict_and_str(self):
        backend, _ = self._backend()
        backend.set("k1", {"company_name": "Acme", "skills": ["Python"]})
        backend.set("k2", "a report string")
        self.assertEqual(backend.get("k1"), {"company_name": "Acme", "skills": ["Python"]})
        self.assertEqual(backend.get("k2"), "a report string")

    def test_get_missing_returns_none(self):
        backend, _ = self._backend()
        self.assertIsNone(backend.get("nope"))

    def test_namespaced_keys(self):
        backend, client = self._backend()
        backend.set("abc", 1)
        self.assertTrue(any(k.startswith("joc:cache:") for k in client.kv))

    def test_clear_only_touches_namespace(self):
        backend, client = self._backend()
        client.kv["unrelated"] = "x"  # not in our namespace
        backend.set("k", 1)
        backend.clear()
        self.assertIn("unrelated", client.kv)
        self.assertFalse(any(k.startswith("joc:cache:") for k in client.kv))

    def test_unserialisable_value_is_skipped_not_raised(self):
        backend, _ = self._backend()
        backend.set("k", object())  # not JSON-serialisable
        # default=str makes most things serialisable; a bare object() becomes
        # its repr string rather than raising — assert no exception + a value.
        self.assertIsNotNone(backend.get("k"))


# ---------------------------------------------------------------------------
# Checkpoint store
# ---------------------------------------------------------------------------

class CheckpointFactoryTests(unittest.TestCase):
    def setUp(self):
        for k in ("REDIS_URL", "CELERY_BROKER_URL"):
            os.environ.pop(k, None)

    def test_defaults_to_in_memory(self):
        import services.checkpoint as ckpt
        self.assertIsInstance(ckpt._build_store(), ckpt.CheckpointStore)

    def test_uses_redis_when_configured(self):
        import services.checkpoint as ckpt
        fake, _ = _install_fake_redis()
        with mock.patch.dict(os.environ, {"REDIS_URL": "redis://localhost:6379/0"}), \
             mock.patch.dict(sys.modules, {"redis": fake}):
            self.assertIsInstance(ckpt._build_store(), ckpt.RedisCheckpointStore)


class RedisCheckpointStoreTests(unittest.TestCase):
    def _store(self):
        import services.checkpoint as ckpt
        fake, client = _install_fake_redis()
        with mock.patch.dict(sys.modules, {"redis": fake}):
            return ckpt.RedisCheckpointStore("redis://localhost:6379/0"), client

    def test_set_get_and_has(self):
        store, _ = self._store()
        self.assertFalse(store.has("key"))
        store.set("key", "job_details", {"extracted_details": {"company_name": "Acme"}})
        self.assertTrue(store.has("key"))
        payload = store.get("key")
        self.assertTrue(payload.has("job_details"))
        self.assertEqual(payload.get("job_details")["extracted_details"]["company_name"], "Acme")

    def test_completed_stages_in_canonical_order(self):
        from services.checkpoint import CHECKPOINT_STAGES
        store, _ = self._store()
        # Write out of order.
        store.set("k", "salary_analysis", {"x": 1})
        store.set("k", "job_details", {"y": 1})
        store.set("k", "company_analysis", {"z": 1})
        done = store.completed_stages("k")
        self.assertEqual(
            done,
            tuple(s for s in CHECKPOINT_STAGES
                  if s in ("job_details", "company_analysis", "salary_analysis")),
        )

    def test_clear_removes_key(self):
        store, _ = self._store()
        store.set("k", "job_details", {"a": 1})
        store.clear("k")
        self.assertFalse(store.has("k"))

    def test_keys_isolated(self):
        store, _ = self._store()
        store.set("alice", "job_details", {"co": "a"})
        store.set("bob", "job_details", {"co": "b"})
        self.assertEqual(store.get("alice").get("job_details")["co"], "a")
        self.assertEqual(store.get("bob").get("job_details")["co"], "b")


if __name__ == "__main__":
    unittest.main()
