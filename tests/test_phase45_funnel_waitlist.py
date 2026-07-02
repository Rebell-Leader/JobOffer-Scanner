"""Validation-unblockers: operator funnel report + public waitlist capture."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from db.session import get_session, reset_engine_for_testing  # noqa: E402


def _fresh():
    reset_engine_for_testing("sqlite:///:memory:")
    from services.rate_limit import reset_backend_for_testing
    reset_backend_for_testing()


def _mk_user(email: str, created: datetime):
    from db.models import User
    from services.auth import _hash_password
    with get_session() as session:
        u = User(email=email, password_hash=_hash_password("x"),
                 email_verified=True, created_at=created)
        session.add(u)
        session.commit()
        return u.id


def _mk_analyses(user_id: int, n: int, at: datetime):
    from db.models import UsageEvent
    with get_session() as session:
        for _ in range(n):
            session.add(UsageEvent(user_id=user_id, kind="analysis", created_at=at))
        session.commit()


def _mk_sub(user_id: int, tier: str, status: str = "active"):
    from db.models import Subscription
    with get_session() as session:
        session.add(Subscription(user_id=user_id, tier=tier, status=status))
        session.commit()


class FunnelTests(unittest.TestCase):
    def setUp(self):
        _fresh()

    def test_empty_db_safe(self):
        from services.funnel import compute_funnel, render_text
        r = compute_funnel(window_days=30)
        self.assertEqual(r.total_users, 0)
        self.assertEqual(r.activation_rate, 0.0)
        self.assertIn("Funnel report", render_text(r))

    def test_activation_and_aha(self):
        from services.funnel import compute_funnel
        now = datetime.utcnow()
        # u1: activated + aha (3 analyses on signup day)
        u1 = _mk_user("a@x.com", now - timedelta(days=2))
        _mk_analyses(u1, 3, now - timedelta(days=2))
        # u2: activated but not aha (1 analysis)
        u2 = _mk_user("b@x.com", now - timedelta(days=2))
        _mk_analyses(u2, 1, now - timedelta(days=1))
        # u3: never ran anything
        _mk_user("c@x.com", now - timedelta(days=1))

        r = compute_funnel(window_days=30, now=now)
        self.assertEqual(r.total_users, 3)
        self.assertEqual(r.activated_users, 2)
        self.assertEqual(r.aha_users, 1)
        self.assertEqual(r.active_users_in_window, 2)
        self.assertEqual(r.analyses_in_window, 4)
        self.assertAlmostEqual(r.avg_analyses_per_active, 2.0)

    def test_aha_window_is_first_week_of_signup(self):
        from services.funnel import compute_funnel
        now = datetime.utcnow()
        u = _mk_user("late@x.com", now - timedelta(days=40))
        # 3 analyses but 20 days AFTER signup -> not aha.
        _mk_analyses(u, 3, now - timedelta(days=20))
        r = compute_funnel(window_days=60, now=now)
        self.assertEqual(r.activated_users, 1)
        self.assertEqual(r.aha_users, 0)

    def test_paying_and_mrr(self):
        from services.funnel import compute_funnel
        now = datetime.utcnow()
        u1 = _mk_user("p@x.com", now - timedelta(days=2))
        _mk_sub(u1, "pro")
        u2 = _mk_user("q@x.com", now - timedelta(days=2))
        _mk_sub(u2, "power")
        _mk_user("f@x.com", now - timedelta(days=2))  # free
        r = compute_funnel(window_days=30, now=now)
        self.assertEqual(r.paying_users, 2)
        self.assertAlmostEqual(r.est_mrr_usd, 36.0)  # 12 + 24
        self.assertAlmostEqual(r.free_to_paid_rate, round(2 / 3, 4))

    def test_canceled_sub_not_counted_paying(self):
        from services.funnel import compute_funnel
        u = _mk_user("c@x.com", datetime.utcnow())
        _mk_sub(u, "pro", status="canceled")
        r = compute_funnel()
        self.assertEqual(r.paying_users, 0)

    def test_cli_runs(self):
        from worker import funnel_report
        self.assertEqual(funnel_report.main(["--days", "7"]), 0)
        self.assertEqual(funnel_report.main(["--json"]), 0)


class WaitlistServiceTests(unittest.TestCase):
    def setUp(self):
        _fresh()

    def test_join_and_count(self):
        from services.waitlist import join_waitlist, waitlist_count
        res = join_waitlist("Me@Example.com ", source="landing")
        self.assertTrue(res.ok)
        self.assertFalse(res.already)
        self.assertEqual(waitlist_count(), 1)

    def test_dedupe_is_friendly_noop(self):
        from services.waitlist import join_waitlist, waitlist_count
        join_waitlist("dup@example.com")
        res = join_waitlist("DUP@example.com")  # normalised -> same row
        self.assertTrue(res.ok)
        self.assertTrue(res.already)
        self.assertEqual(waitlist_count(), 1)

    def test_bad_email_rejected(self):
        from services.waitlist import WaitlistError, join_waitlist
        for bad in ("", "nope", "a@b", "a b@c.com"):
            with self.assertRaises(WaitlistError):
                join_waitlist(bad)

    def test_rate_limited(self):
        from services.rate_limit import RateLimitExceeded
        from services.waitlist import join_waitlist
        with self.assertRaises(RateLimitExceeded):
            for i in range(25):
                join_waitlist(f"user{i}@example.com", rate_key="same-bucket")


class WaitlistEndpointTests(unittest.TestCase):
    def setUp(self):
        _fresh()

    def _client(self):
        from fastapi.testclient import TestClient

        from api.main import create_app
        return TestClient(create_app())

    def test_post_ok(self):
        r = self._client().post("/waitlist", json={"email": "new@example.com",
                                                    "source": "landing"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"ok": True, "already": False})

    def test_post_duplicate_is_200_already(self):
        c = self._client()
        c.post("/waitlist", json={"email": "d@example.com"})
        r = c.post("/waitlist", json={"email": "d@example.com"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["already"])

    def test_post_bad_email_400(self):
        r = self._client().post("/waitlist", json={"email": "notanemail"})
        self.assertEqual(r.status_code, 400)

    def test_unauthenticated_allowed(self):
        # No Authorization header — public endpoint.
        r = self._client().post("/waitlist", json={"email": "anon@example.com"})
        self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()
