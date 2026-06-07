"""P0.3 (part 1): account deletion + data export + cascade integrity."""

from __future__ import annotations

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _fresh_db():
    from db.session import reset_engine_for_testing
    from services.rate_limit import reset_backend_for_testing
    reset_engine_for_testing("sqlite:///:memory:")
    reset_backend_for_testing()


def _seed(user_email="u@x.com"):
    """A user with data across many child tables, to test cascade + export."""
    from datetime import date
    from services.applications import save_analysis
    from services.auth import register_user
    from services.api_tokens import issue
    from services.master_cv import save_master_cv
    from services.projects import create_project
    from services.sharing import create_share
    from services.stages import add_stage
    from services.tailoring import save_artifact
    from services.webhooks import register_webhook

    user = register_user(user_email, "longenough")
    save_master_cv(user.id, "Jane Doe\nSkills: Python")
    save_master_cv(user.id, "Jane Doe\nSkills: Python, AWS")  # creates a revision
    create_project(user.id, title="recsys", tech_stack="Python")
    app = save_analysis(
        user.id,
        {"company_name": "Acme", "job_title": "Eng", "location": "Berlin"},
        {"final_report": "# r", "verdict": {"verdict": "Recommended", "light": "green"},
         "resume_analysis": {"ats_score": 70}},
    )
    add_stage(user.id, app.id, "applied", occurred_on=date(2026, 5, 1))
    save_artifact(user.id, app.id, "tailored_cv", "# CV", meta={"model": "fast"})
    create_share(user.id, app.id, ttl_days=7)
    issue(user.id, "cli")
    register_webhook(user.id, "https://x.com/h", ["stage.added"])
    return user, app


# ---------------------------------------------------------------------------
# SQLite FK pragma (cascade enforcement)
# ---------------------------------------------------------------------------

class SqliteForeignKeyTests(unittest.TestCase):
    def test_foreign_keys_pragma_enabled(self):
        _fresh_db()
        from sqlalchemy import text
        from db.session import get_session
        with get_session() as s:
            val = s.execute(text("PRAGMA foreign_keys")).scalar()
        self.assertEqual(val, 1)


# ---------------------------------------------------------------------------
# Account deletion
# ---------------------------------------------------------------------------

class DeleteAccountTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        self.user, self.app = _seed()

    def test_wrong_password_rejected(self):
        from services.auth import AuthError, delete_account
        with self.assertRaises(AuthError):
            delete_account(self.user.id, "wrongpassword")
        # Still there.
        from services.auth import get_user
        self.assertIsNotNone(get_user(self.user.id))

    def test_delete_removes_user_and_cascades_children(self):
        from sqlalchemy import text
        from db.session import get_session
        from services.auth import delete_account, get_user

        delete_account(self.user.id, "longenough")
        self.assertIsNone(get_user(self.user.id))

        with get_session() as s:
            # Tables that carry user_id directly.
            user_scoped = [
                "applications", "application_artifacts", "application_shares",
                "master_cvs", "master_cv_revisions", "projects", "api_tokens",
                "webhooks", "telegram_links", "background_analyses",
            ]
            for tbl in user_scoped:
                n = s.execute(
                    text(f"SELECT COUNT(*) FROM {tbl} WHERE user_id = :u"),
                    {"u": self.user.id},
                ).scalar()
                self.assertEqual(n, 0, msg=f"{tbl} not cascaded ({n} rows left)")
            # application_stages is app-scoped (no user_id) — the only app was
            # the deleted user's, so the table must be empty (cascade from
            # applications -> stages).
            stages_left = s.execute(text("SELECT COUNT(*) FROM application_stages")).scalar()
            self.assertEqual(stages_left, 0, msg="application_stages not cascaded")

    def test_delete_is_audited_and_audit_survives(self):
        from services.audit import list_recent
        from services.auth import delete_account

        delete_account(self.user.id, "longenough")
        # The audit row's user_id is SET NULL by the cascade, but the record
        # (with the email in details) survives.
        recent = list_recent(limit=50)
        match = next(
            (e for e in recent
             if e.kind == "user.account.delete"
             and e.details.get("email") == "u@x.com"),
            None,
        )
        self.assertIsNotNone(match)
        self.assertIsNone(match.user_id)  # SET NULL on cascade

    def test_delete_only_affects_target_user(self):
        from services.applications import list_applications
        from services.auth import delete_account

        other, _ = _seed("other@x.com")
        delete_account(self.user.id, "longenough")
        # Other user's data is intact.
        self.assertEqual(len(list_applications(other.id)), 1)


# ---------------------------------------------------------------------------
# Data export
# ---------------------------------------------------------------------------

class ExportTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        self.user, self.app = _seed()

    def test_export_bundles_all_domains(self):
        from services.account_export import build_export

        b = build_export(self.user.id)
        self.assertEqual(b["account"]["email"], "u@x.com")
        self.assertEqual(len(b["applications"]), 1)
        app = b["applications"][0]
        self.assertEqual(app["company_name"], "Acme")
        self.assertEqual(len(app["stages"]), 1)
        self.assertEqual(len(app["artifacts"]), 1)
        self.assertEqual(len(app["shares"]), 1)
        self.assertIsNotNone(b["master_cv"])
        self.assertEqual(len(b["master_cv_revisions"]), 1)
        self.assertEqual(len(b["projects"]), 1)
        self.assertEqual(len(b["api_tokens"]), 1)
        self.assertEqual(len(b["webhooks"]), 1)

    def test_export_excludes_secrets(self):
        """The bundle must not leak password hash, token hash, or webhook secret."""
        from services.account_export import export_json

        blob = export_json(self.user.id)
        lowered = blob.lower()
        self.assertNotIn("password_hash", lowered)
        self.assertNotIn("token_hash", lowered)
        # Webhook secret value must not appear: fetch it and assert absence.
        from services.webhooks import list_webhooks
        secret = list_webhooks(self.user.id)[0].secret
        self.assertNotIn(secret, blob)

    def test_export_is_valid_json(self):
        from services.account_export import export_json

        data = json.loads(export_json(self.user.id))  # must parse
        self.assertIn("exported_at", data)

    def test_export_records_audit(self):
        from services.account_export import export_json
        from services.audit import list_for_user

        export_json(self.user.id)
        kinds = {e.kind for e in list_for_user(self.user.id)}
        self.assertIn("user.account.export", kinds)


if __name__ == "__main__":
    unittest.main()
