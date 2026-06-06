"""Unit tests for Phase 3: auth + application persistence (in-memory SQLite)."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _fresh_db():
    """Re-init the global engine against an in-memory SQLite for each test."""
    from db.session import reset_engine_for_testing
    reset_engine_for_testing("sqlite:///:memory:")


class AuthTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()

    def test_register_then_authenticate(self):
        from services.auth import authenticate_user, register_user

        user = register_user("Alice@Example.com", "supersecret")
        self.assertEqual(user.email, "alice@example.com")  # normalized

        again = authenticate_user("alice@example.com", "supersecret")
        self.assertEqual(again.id, user.id)

    def test_rejects_short_password(self):
        from services.auth import AuthError, register_user

        with self.assertRaises(AuthError):
            register_user("a@b.com", "short")

    def test_rejects_invalid_email(self):
        from services.auth import AuthError, register_user

        with self.assertRaises(AuthError):
            register_user("not-an-email", "longenough")

    def test_rejects_duplicate_email(self):
        from services.auth import AuthError, register_user

        register_user("dup@x.com", "longenough")
        with self.assertRaises(AuthError):
            register_user("DUP@x.com", "anotherone")  # case-insensitive dup

    def test_wrong_password_fails(self):
        from services.auth import AuthError, authenticate_user, register_user

        register_user("b@x.com", "longenough")
        with self.assertRaises(AuthError):
            authenticate_user("b@x.com", "wrongguess")

    def test_unknown_user_same_error_as_bad_password(self):
        """No user-enumeration: identical message for both failures."""
        from services.auth import AuthError, authenticate_user, register_user

        register_user("known@x.com", "longenough")
        with self.assertRaises(AuthError) as ctx_unknown:
            authenticate_user("unknown@x.com", "anything12")
        with self.assertRaises(AuthError) as ctx_badpw:
            authenticate_user("known@x.com", "wrongguess")
        self.assertEqual(str(ctx_unknown.exception), str(ctx_badpw.exception))

    def test_password_not_stored_in_plaintext(self):
        from db.models import User
        from db.session import get_session
        from services.auth import register_user
        from sqlalchemy import select

        register_user("c@x.com", "longenough")
        with get_session() as s:
            row = s.execute(select(User).where(User.email == "c@x.com")).scalar_one()
            self.assertNotEqual(row.password_hash, "longenough")
            # bcrypt hashes start with $2 and are >50 chars.
            self.assertTrue(row.password_hash.startswith("$2"))
            self.assertGreater(len(row.password_hash), 50)


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        from services.auth import register_user
        self.user = register_user("user@x.com", "longenough")
        self.other = register_user("other@x.com", "longenough")
        self.sample_result = {
            "final_report": "# Report\n\nGood fit.",
            "verdict": {"verdict": "Recommended", "light": "green", "reasons": ["a"]},
            "resume_analysis": {"ats_score": 75, "matched_skills": ["Python"]},
            "job_details": {},
            "company_analysis": {},
            "salary_analysis": {},
        }
        self.sample_inputs = {
            "company_name": "Acme",
            "job_title": "ML Engineer",
            "location": "Berlin",
            "compensation": "EUR 80k",
        }

    def test_save_and_list(self):
        from services.applications import list_applications, save_analysis

        saved = save_analysis(self.user.id, self.sample_inputs, self.sample_result)
        self.assertEqual(saved.company_name, "Acme")
        self.assertEqual(saved.verdict, "Recommended")
        self.assertEqual(saved.verdict_light, "green")
        self.assertEqual(saved.ats_score, 75)

        rows = list_applications(self.user.id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].id, saved.id)

    def test_list_is_isolated_per_user(self):
        from services.applications import list_applications, save_analysis

        save_analysis(self.user.id, self.sample_inputs, self.sample_result)
        self.assertEqual(len(list_applications(self.other.id)), 0)

    def test_update_status_and_notes(self):
        from services.applications import save_analysis, update_status

        saved = save_analysis(self.user.id, self.sample_inputs, self.sample_result)
        updated = update_status(self.user.id, saved.id, status="applied", notes="Sent CV")
        self.assertEqual(updated.status, "applied")
        self.assertEqual(updated.notes, "Sent CV")

    def test_update_rejects_unknown_status(self):
        from services.applications import ApplicationError, save_analysis, update_status

        saved = save_analysis(self.user.id, self.sample_inputs, self.sample_result)
        with self.assertRaises(ApplicationError):
            update_status(self.user.id, saved.id, status="ghosted")

    def test_cross_user_access_blocked(self):
        from services.applications import (
            ApplicationError,
            delete_application,
            get_application,
            save_analysis,
            update_status,
        )

        saved = save_analysis(self.user.id, self.sample_inputs, self.sample_result)
        for op in (
            lambda: get_application(self.other.id, saved.id),
            lambda: update_status(self.other.id, saved.id, status="applied"),
            lambda: delete_application(self.other.id, saved.id),
        ):
            with self.assertRaises(ApplicationError):
                op()

    def test_save_requires_company_and_title(self):
        from services.applications import ApplicationError, save_analysis

        with self.assertRaises(ApplicationError):
            save_analysis(self.user.id, {"company_name": "", "job_title": "X"}, self.sample_result)
        with self.assertRaises(ApplicationError):
            save_analysis(self.user.id, {"company_name": "X", "job_title": ""}, self.sample_result)

    def test_serializable_strips_callables(self):
        """Progress callbacks must not break JSON serialization."""
        from services.applications import save_analysis

        result_with_callback = dict(self.sample_result)
        result_with_callback["progress_callback"] = lambda *a, **kw: None
        saved = save_analysis(self.user.id, self.sample_inputs, result_with_callback)
        self.assertNotIn("progress_callback", saved.analysis_json)

    def test_delete_removes_record(self):
        from services.applications import delete_application, list_applications, save_analysis

        saved = save_analysis(self.user.id, self.sample_inputs, self.sample_result)
        delete_application(self.user.id, saved.id)
        self.assertEqual(list_applications(self.user.id), [])


if __name__ == "__main__":
    unittest.main()
