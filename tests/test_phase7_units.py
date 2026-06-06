"""Unit tests for Phase 7: rate limiting + bot async-queue integration."""

from __future__ import annotations

import asyncio
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _fresh_db():
    from db.session import reset_engine_for_testing
    from services.rate_limit import reset_backend_for_testing
    reset_engine_for_testing("sqlite:///:memory:")
    reset_backend_for_testing()


def _reset_rate_limit():
    # Wipe any state from earlier tests / earlier modules.
    from services.rate_limit import reset_backend_for_testing
    reset_backend_for_testing()


# ---------------------------------------------------------------------------
# Rate-limit primitive
# ---------------------------------------------------------------------------

class RateLimiterCoreTests(unittest.TestCase):
    def setUp(self):
        # Force the in-memory backend regardless of host env.
        os.environ.pop("REDIS_URL", None)
        os.environ.pop("CELERY_BROKER_URL", None)
        _reset_rate_limit()

    def test_allows_up_to_limit_then_denies(self):
        from services.rate_limit import RateLimiter

        limiter = RateLimiter("t", max_attempts=3, window_seconds=60)
        for _ in range(3):
            self.assertTrue(limiter.check("a").allowed)
        denied = limiter.check("a")
        self.assertFalse(denied.allowed)
        self.assertGreater(denied.retry_after, 0)

    def test_different_keys_isolated(self):
        from services.rate_limit import RateLimiter

        limiter = RateLimiter("t", max_attempts=2, window_seconds=60)
        limiter.check("a")
        limiter.check("a")
        self.assertFalse(limiter.check("a").allowed)
        # A separate key still has full budget.
        self.assertTrue(limiter.check("b").allowed)

    def test_window_rolls(self):
        from services.rate_limit import RateLimiter

        limiter = RateLimiter("t", max_attempts=2, window_seconds=0.05)
        limiter.check("a")
        limiter.check("a")
        self.assertFalse(limiter.check("a").allowed)
        time.sleep(0.06)
        # Window elapsed — counter is empty again.
        self.assertTrue(limiter.check("a").allowed)

    def test_reset_clears_counter(self):
        from services.rate_limit import RateLimiter

        limiter = RateLimiter("t", max_attempts=1, window_seconds=60)
        limiter.check("a")
        self.assertFalse(limiter.check("a").allowed)
        limiter.reset("a")
        self.assertTrue(limiter.check("a").allowed)


# ---------------------------------------------------------------------------
# Auth limiter integration
# ---------------------------------------------------------------------------

class AuthLimitingTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop("REDIS_URL", None)
        os.environ.pop("CELERY_BROKER_URL", None)
        # Tight limits so the test runs fast and deterministic.
        os.environ["RL_LOGIN_MAX"] = "3"
        os.environ["RL_LOGIN_WINDOW"] = "60"
        os.environ["RL_REGISTER_MAX"] = "2"
        os.environ["RL_REGISTER_WINDOW"] = "60"
        os.environ["RL_RESET_MAX"] = "2"
        os.environ["RL_RESET_WINDOW"] = "60"
        # Re-import services.rate_limit so the env-driven defaults take effect,
        # then rebuild the limiters in services.auth too.
        for mod_name in ("services.rate_limit", "services.auth"):
            sys.modules.pop(mod_name, None)
        _reset_rate_limit()
        _fresh_db()
        from services.auth import register_user  # noqa: F401 - re-imports below
        self._register = register_user

    def test_login_blocks_after_max_failures(self):
        from services.auth import AuthError, authenticate_user
        from services.rate_limit import RateLimitExceeded

        self._register("a@x.com", "longenough")
        # 3 wrong attempts: all fail with AuthError, then the 4th gets blocked.
        for _ in range(3):
            with self.assertRaises(AuthError):
                authenticate_user("a@x.com", "wrongguess")
        with self.assertRaises(RateLimitExceeded):
            authenticate_user("a@x.com", "wrongguess")

    def test_successful_login_resets_counter(self):
        from services.auth import AuthError, authenticate_user

        self._register("b@x.com", "longenough")
        # Two failures, then one success → counter reset → can fail twice more.
        for _ in range(2):
            with self.assertRaises(AuthError):
                authenticate_user("b@x.com", "wrongguess")
        authenticate_user("b@x.com", "longenough")
        for _ in range(2):
            with self.assertRaises(AuthError):
                authenticate_user("b@x.com", "wrongguess")

    def test_register_blocks_after_max(self):
        from services.auth import AuthError, register_user
        from services.rate_limit import RateLimitExceeded

        # Two attempts (one OK, one duplicate-error) consume the budget.
        register_user("c@x.com", "longenough")
        with self.assertRaises(AuthError):
            register_user("c@x.com", "longenough")  # duplicate
        # Third attempt is rate-limited (same email).
        with self.assertRaises(RateLimitExceeded):
            register_user("c@x.com", "different1")

    def test_reset_request_blocked_after_max(self):
        from services.auth import register_user, request_password_reset
        from services.rate_limit import RateLimitExceeded

        register_user("d@x.com", "longenough")
        request_password_reset("d@x.com")
        request_password_reset("d@x.com")
        with self.assertRaises(RateLimitExceeded):
            request_password_reset("d@x.com")


# ---------------------------------------------------------------------------
# Analysis runner quota
# ---------------------------------------------------------------------------

class AnalysisQuotaTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop("REDIS_URL", None)
        os.environ.pop("CELERY_BROKER_URL", None)
        os.environ["RL_ANALYSIS_MAX"] = "2"
        os.environ["RL_ANALYSIS_WINDOW"] = "60"
        for mod_name in ("services.rate_limit", "services.analysis_runner"):
            sys.modules.pop(mod_name, None)
        _reset_rate_limit()

    def test_quota_blocks_after_max(self):
        from services.analysis_runner import check_user_quota
        from services.rate_limit import RateLimitExceeded

        check_user_quota(42)
        check_user_quota(42)
        with self.assertRaises(RateLimitExceeded):
            check_user_quota(42)

    def test_quota_isolated_per_user(self):
        from services.analysis_runner import check_user_quota

        check_user_quota(1)
        check_user_quota(1)
        # A different user still has full budget.
        check_user_quota(2)
        check_user_quota(2)

    def test_quota_none_user_id_skips(self):
        from services.analysis_runner import check_user_quota

        for _ in range(10):
            check_user_quota(None)  # bot/anon flow — never raises


# ---------------------------------------------------------------------------
# Bot → async queue routing
# ---------------------------------------------------------------------------

class BotQueueRoutingTests(unittest.TestCase):
    def test_bot_uses_queue_when_async_enabled(self):
        from bot import handlers

        sent = []

        async def reply(text):
            sent.append(text)

        fake_result = {
            "verdict": {"verdict": "Recommended", "light": "green", "reasons": []},
            "job_details": {"extracted_details": {"company_name": "Acme"}},
            "final_report": "# Report\n\nGreat.",
        }

        with mock.patch.object(handlers, "async_enabled", return_value=True), \
             mock.patch.object(handlers, "enqueue_analysis", return_value="task-123"), \
             mock.patch.object(handlers, "get_async_result",
                               return_value=("SUCCESS", fake_result)):
            asyncio.run(handlers.handle_analyze(reply, "Company: Acme\nTitle: Eng"))

        joined = "\n".join(sent)
        self.assertIn("Recommended", joined)
        self.assertIn("Great.", joined)

    def test_bot_falls_back_to_sync_when_no_queue(self):
        from bot import handlers

        sent = []

        async def reply(text):
            sent.append(text)

        fake_result = {
            "verdict": {"verdict": "Recommended", "light": "green", "reasons": []},
            "job_details": {"extracted_details": {"company_name": "Acme"}},
            "final_report": "# Report\n\nSync path.",
        }

        with mock.patch.object(handlers, "async_enabled", return_value=False), \
             mock.patch.object(handlers, "run_analysis", return_value=fake_result), \
             mock.patch.object(handlers, "enqueue_analysis") as enq:
            asyncio.run(handlers.handle_analyze(reply, "Company: Acme\nTitle: Eng"))

        enq.assert_not_called()
        self.assertIn("Sync path.", "\n".join(sent))

    def test_bot_reports_task_timeout(self):
        from bot import handlers

        sent = []

        async def reply(text):
            sent.append(text)

        # Patch the poll interval/timeout so the test doesn't actually wait 600s.
        with mock.patch.object(handlers, "_BOT_POLL_INTERVAL", 0.001), \
             mock.patch.object(handlers, "_BOT_POLL_TIMEOUT", 0.01), \
             mock.patch.object(handlers, "async_enabled", return_value=True), \
             mock.patch.object(handlers, "enqueue_analysis", return_value="task-xyz"), \
             mock.patch.object(handlers, "get_async_result",
                               return_value=("PENDING", None)):
            asyncio.run(handlers.handle_analyze(reply, "Company: Acme\nTitle: Eng"))

        self.assertTrue(any("did not finish" in m for m in sent))

    def test_bot_reports_task_failure(self):
        from bot import handlers

        sent = []

        async def reply(text):
            sent.append(text)

        with mock.patch.object(handlers, "async_enabled", return_value=True), \
             mock.patch.object(handlers, "enqueue_analysis", return_value="task-fail"), \
             mock.patch.object(handlers, "get_async_result",
                               return_value=("FAILURE", "boom")):
            asyncio.run(handlers.handle_analyze(reply, "Company: Acme\nTitle: Eng"))

        joined = "\n".join(sent)
        self.assertTrue(
            "ended in state FAILURE" in joined or "Analysis failed" in joined,
            msg=f"unexpected reply set: {sent!r}",
        )


if __name__ == "__main__":
    unittest.main()
