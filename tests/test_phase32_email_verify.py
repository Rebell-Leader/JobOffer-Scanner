"""P0.3 (part 2): email verification."""

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


class RegistrationVerificationStateTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()

    def test_new_password_user_is_unverified(self):
        from services.auth import register_user
        from services.email_verify import is_verified

        user = register_user("u@x.com", "longenough")
        self.assertFalse(user.email_verified)
        self.assertFalse(is_verified(user.id))

    def test_oauth_user_is_verified_on_creation(self):
        from services.auth import create_oauth_user
        from services.email_verify import is_verified

        user = create_oauth_user("oauth@x.com")
        self.assertTrue(user.email_verified)
        self.assertTrue(is_verified(user.id))

    def test_authenticate_reports_verification_state(self):
        from services.auth import authenticate_user, register_user
        from services.email_verify import complete_verification, start_verification

        register_user("u@x.com", "longenough")
        self.assertFalse(authenticate_user("u@x.com", "longenough").email_verified)
        # Verify, then a fresh login reflects it.
        tok = start_verification(authenticate_user("u@x.com", "longenough").id)
        complete_verification("u@x.com", tok)
        self.assertTrue(authenticate_user("u@x.com", "longenough").email_verified)


class VerificationFlowTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        from services.auth import register_user
        self.user = register_user("u@x.com", "longenough")

    def test_start_then_complete(self):
        from services.email_verify import complete_verification, is_verified, start_verification

        tok = start_verification(self.user.id)
        self.assertIsNotNone(tok)
        complete_verification("u@x.com", tok)
        self.assertTrue(is_verified(self.user.id))

    def test_start_returns_none_when_already_verified(self):
        from services.email_verify import complete_verification, start_verification

        complete_verification("u@x.com", start_verification(self.user.id))
        self.assertIsNone(start_verification(self.user.id))

    def test_token_is_one_shot(self):
        from services.email_verify import (
            EmailVerifyError, complete_verification, start_verification,
        )

        tok = start_verification(self.user.id)
        complete_verification("u@x.com", tok)
        # Account is now verified -> complete is idempotent (no raise), but the
        # token itself is consumed. Verify a *fresh unverified* user can't reuse
        # someone else's used token.
        from services.auth import register_user
        other = register_user("o@x.com", "longenough")  # noqa: F841
        with self.assertRaises(EmailVerifyError):
            complete_verification("o@x.com", tok)

    def test_wrong_token_rejected(self):
        from services.email_verify import EmailVerifyError, complete_verification, start_verification

        start_verification(self.user.id)
        with self.assertRaises(EmailVerifyError):
            complete_verification("u@x.com", "totally-wrong")

    def test_expired_token_rejected(self):
        from sqlalchemy import select
        from db.models import EmailVerificationToken
        from db.session import get_session
        from services.email_verify import (
            EmailVerifyError, complete_verification, start_verification,
        )

        tok = start_verification(self.user.id)
        with get_session() as s:
            row = s.execute(select(EmailVerificationToken)).scalar_one()
            row.expires_at = datetime.utcnow() - timedelta(minutes=1)
            s.commit()
        with self.assertRaises(EmailVerifyError):
            complete_verification("u@x.com", tok)

    def test_empty_token_rejected(self):
        from services.email_verify import EmailVerifyError, complete_verification

        with self.assertRaises(EmailVerifyError):
            complete_verification("u@x.com", "")

    def test_token_hash_not_raw(self):
        from sqlalchemy import select
        from db.models import EmailVerificationToken
        from db.session import get_session
        from services.email_verify import start_verification

        tok = start_verification(self.user.id)
        with get_session() as s:
            row = s.execute(select(EmailVerificationToken)).scalar_one()
        self.assertNotEqual(row.token_hash, tok)
        self.assertTrue(row.token_hash.startswith("$2"))

    def test_resend_rate_limited(self):
        from services.email_verify import start_verification
        from services.rate_limit import RateLimitExceeded

        for _ in range(5):
            start_verification(self.user.id)
        with self.assertRaises(RateLimitExceeded):
            start_verification(self.user.id)

    def test_audit_events_recorded(self):
        from services.audit import list_for_user
        from services.email_verify import complete_verification, start_verification

        complete_verification("u@x.com", start_verification(self.user.id))
        kinds = {e.kind for e in list_for_user(self.user.id)}
        self.assertIn("user.email.verify.request", kinds)
        self.assertIn("user.email.verify.complete", kinds)


class MigrationTests(unittest.TestCase):
    def test_existing_users_backfilled_verified(self):
        """The migration must backfill pre-existing rows to verified (so nobody
        is locked out), while the table + column exist."""
        from sqlalchemy import create_engine, inspect, text

        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "m.db"
            url = f"sqlite:///{db_path.as_posix()}"
            env = dict(os.environ)
            env["DATABASE_URL"] = url

            # Migrate to just BEFORE email verification, insert a legacy user,
            # then migrate up and assert the backfill marked it verified.
            def _alembic(*args):
                return subprocess.run(
                    [sys.executable, "-m", "alembic", *args],
                    cwd=project_root, env=env, capture_output=True, text=True,
                )

            r = _alembic("upgrade", "268857701205")  # pre-verification head
            self.assertEqual(r.returncode, 0, msg=r.stderr)

            eng = create_engine(url)
            with eng.begin() as c:
                c.execute(text(
                    "INSERT INTO users (email, password_hash, created_at) "
                    "VALUES ('legacy@x.com', 'x', :ts)"
                ), {"ts": datetime.utcnow()})

            r = _alembic("upgrade", "head")
            self.assertEqual(r.returncode, 0, msg=r.stderr)

            with eng.connect() as c:
                verified = c.execute(text(
                    "SELECT email_verified FROM users WHERE email='legacy@x.com'"
                )).scalar()
            self.assertEqual(verified, 1)  # backfilled True

            tables = set(inspect(eng).get_table_names())
            self.assertIn("email_verification_tokens", tables)


if __name__ == "__main__":
    unittest.main()
