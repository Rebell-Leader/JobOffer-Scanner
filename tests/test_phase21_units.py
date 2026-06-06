"""Unit tests for Phase 21: public application share links."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _fresh_db():
    from db.session import reset_engine_for_testing
    from services.rate_limit import reset_backend_for_testing
    reset_engine_for_testing("sqlite:///:memory:")
    reset_backend_for_testing()


def _register(email="u@x.com"):
    from services.auth import register_user
    return register_user(email, "longenough")


def _save_app(user_id, company="Acme", title="ML Engineer"):
    from services.applications import save_analysis
    return save_analysis(
        user_id,
        {"company_name": company, "job_title": title, "location": "Berlin"},
        {
            "final_report": "# Shared report content",
            "verdict": {"verdict": "Recommended", "light": "green", "reasons": []},
            "resume_analysis": {"ats_score": 75},
        },
    )


# ---------------------------------------------------------------------------
# create / list / revoke
# ---------------------------------------------------------------------------

class ShareLifecycleTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        self.user = _register()
        self.other = _register("other@x.com")
        self.app = _save_app(self.user.id)

    def test_create_returns_active_token(self):
        from services.sharing import create_share

        share = create_share(self.user.id, self.app.id, ttl_days=7)
        self.assertGreater(len(share.token), 30)
        self.assertTrue(share.is_active)
        self.assertIsNotNone(share.expires_at)
        self.assertEqual(share.view_count, 0)
        self.assertFalse(share.include_artifacts)

    def test_create_with_no_ttl_has_no_expiry(self):
        from services.sharing import create_share

        share = create_share(self.user.id, self.app.id, ttl_days=None)
        self.assertIsNone(share.expires_at)
        self.assertTrue(share.is_active)

    def test_create_with_zero_ttl_treated_as_no_expiry(self):
        from services.sharing import create_share

        share = create_share(self.user.id, self.app.id, ttl_days=0)
        self.assertIsNone(share.expires_at)

    def test_create_cross_user_blocked(self):
        from services.sharing import ShareError, create_share

        with self.assertRaises(ShareError):
            create_share(self.other.id, self.app.id)

    def test_list_returns_newest_first(self):
        import time

        from services.sharing import create_share, list_shares_for_application

        create_share(self.user.id, self.app.id, ttl_days=7)
        time.sleep(0.01)
        create_share(self.user.id, self.app.id, ttl_days=14)
        time.sleep(0.01)
        create_share(self.user.id, self.app.id, ttl_days=None)
        rows = list_shares_for_application(self.user.id, self.app.id)
        self.assertEqual(len(rows), 3)
        self.assertGreater(rows[0].created_at, rows[1].created_at)

    def test_list_cross_user_blocked(self):
        from services.sharing import ShareError, list_shares_for_application

        with self.assertRaises(ShareError):
            list_shares_for_application(self.other.id, self.app.id)

    def test_revoke_marks_inactive(self):
        from services.sharing import create_share, list_shares_for_application, revoke

        share = create_share(self.user.id, self.app.id, ttl_days=7)
        revoke(self.user.id, share.id)
        rows = list_shares_for_application(self.user.id, self.app.id)
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0].is_active)
        self.assertIsNotNone(rows[0].revoked_at)

    def test_revoke_cross_user_blocked(self):
        from services.sharing import ShareError, create_share, revoke

        share = create_share(self.user.id, self.app.id, ttl_days=7)
        with self.assertRaises(ShareError):
            revoke(self.other.id, share.id)


# ---------------------------------------------------------------------------
# Public read path
# ---------------------------------------------------------------------------

class ShareViewTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        self.user = _register()
        self.app = _save_app(self.user.id, company="Stripe", title="Staff ML Engineer")

    def test_get_view_returns_application_data(self):
        from services.sharing import create_share, get_view

        share = create_share(self.user.id, self.app.id, ttl_days=7)
        view = get_view(share.token)
        self.assertEqual(view.company_name, "Stripe")
        self.assertEqual(view.job_title, "Staff ML Engineer")
        self.assertEqual(view.verdict, "Recommended")
        self.assertEqual(view.ats_score, 75)
        self.assertIn("Shared report content", view.analysis_json["final_report"])
        self.assertEqual(view.artifacts, [])  # default include_artifacts=False

    def test_get_view_includes_artifacts_when_requested(self):
        """include_artifacts=True must surface tailored CVs / cover letters."""
        from unittest import mock

        from services.master_cv import save_master_cv
        from services.sharing import create_share, get_view
        import services.tailoring as tailoring

        save_master_cv(self.user.id, "Jane Doe\nSkills: Python, AWS")
        with mock.patch.object(tailoring, "get_completion", return_value="# tailored content"):
            tailoring.generate_tailored_cv(self.user.id, self.app.id)

        share = create_share(self.user.id, self.app.id, ttl_days=7, include_artifacts=True)
        view = get_view(share.token)
        self.assertEqual(len(view.artifacts), 1)
        self.assertEqual(view.artifacts[0]["kind"], "tailored_cv")
        self.assertIn("tailored content", view.artifacts[0]["content"])

    def test_get_view_rejects_unknown_token(self):
        from services.sharing import ShareError, get_view

        with self.assertRaises(ShareError):
            get_view("not-a-real-token")

    def test_get_view_rejects_revoked_token(self):
        from services.sharing import ShareError, create_share, get_view, revoke

        share = create_share(self.user.id, self.app.id, ttl_days=7)
        revoke(self.user.id, share.id)
        with self.assertRaises(ShareError):
            get_view(share.token)

    def test_get_view_rejects_expired_token(self):
        from db.models import ApplicationShare
        from db.session import get_session
        from services.sharing import ShareError, create_share, get_view
        from sqlalchemy import select

        share = create_share(self.user.id, self.app.id, ttl_days=7)
        # Manually expire.
        with get_session() as s:
            row = s.execute(
                select(ApplicationShare).where(ApplicationShare.id == share.id)
            ).scalar_one()
            row.expires_at = datetime.utcnow() - timedelta(minutes=1)
            s.commit()
        with self.assertRaises(ShareError):
            get_view(share.token)

    def test_get_view_increments_view_count(self):
        from services.sharing import create_share, get_view, list_shares_for_application

        share = create_share(self.user.id, self.app.id, ttl_days=7)
        get_view(share.token)
        get_view(share.token)
        get_view(share.token)
        rows = list_shares_for_application(self.user.id, self.app.id)
        self.assertEqual(rows[0].view_count, 3)
        self.assertIsNotNone(rows[0].last_viewed_at)

    def test_get_view_records_audit_event_on_owner(self):
        from services.audit import list_for_user
        from services.sharing import create_share, get_view

        share = create_share(self.user.id, self.app.id, ttl_days=7)
        get_view(share.token, viewer_ip="1.2.3.4")
        events = list_for_user(self.user.id)
        match = next((e for e in events if e.kind == "share.view"), None)
        self.assertIsNotNone(match)
        self.assertEqual(match.details.get("ip"), "1.2.3.4")
        self.assertEqual(match.details.get("share_id"), share.id)

    def test_empty_token_rejected(self):
        from services.sharing import ShareError, get_view

        with self.assertRaises(ShareError):
            get_view("")
        with self.assertRaises(ShareError):
            get_view("   ")


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

class ShareAuditTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        self.user = _register()
        self.app = _save_app(self.user.id)

    def test_create_records_audit(self):
        from services.audit import list_for_user
        from services.sharing import create_share

        share = create_share(self.user.id, self.app.id, ttl_days=14)
        events = list_for_user(self.user.id)
        match = next((e for e in events if e.kind == "share.create"), None)
        self.assertIsNotNone(match)
        self.assertEqual(match.details.get("share_id"), share.id)
        self.assertEqual(match.details.get("ttl_days"), 14)

    def test_revoke_records_audit(self):
        from services.audit import list_for_user
        from services.sharing import create_share, revoke

        share = create_share(self.user.id, self.app.id, ttl_days=7)
        revoke(self.user.id, share.id)
        events = list_for_user(self.user.id)
        kinds = {e.kind for e in events}
        self.assertIn("share.revoke", kinds)


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

class Phase21MigrationTests(unittest.TestCase):
    def test_table_created(self):
        from sqlalchemy import create_engine, inspect

        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "alembic.db"
            env = dict(os.environ)
            env["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
            result = subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                cwd=project_root, env=env,
                capture_output=True, text=True,
            )
            self.assertEqual(
                result.returncode, 0,
                msg=f"alembic upgrade failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}",
            )
            tables = set(inspect(create_engine(f"sqlite:///{db_path.as_posix()}")).get_table_names())
            self.assertIn("application_shares", tables)


if __name__ == "__main__":
    unittest.main()
