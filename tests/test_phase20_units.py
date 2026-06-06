"""Unit tests for Phase 20: TOTP-based 2FA."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _fresh_db():
    from db.session import reset_engine_for_testing
    from services.rate_limit import reset_backend_for_testing
    reset_engine_for_testing("sqlite:///:memory:")
    reset_backend_for_testing()


def _register(email="u@x.com"):
    from services.auth import register_user
    return register_user(email, "longenough")


def _current_otp(secret: str) -> str:
    import pyotp
    return pyotp.TOTP(secret).now()


# ---------------------------------------------------------------------------
# Setup ceremony
# ---------------------------------------------------------------------------

class SetupTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        self.user = _register()

    def test_start_setup_returns_secret_and_uri(self):
        from services.totp import start_setup

        result = start_setup(self.user.id, self.user.email)
        # Base32 secret >= 16 chars (pyotp default is 32).
        self.assertGreaterEqual(len(result.secret), 16)
        self.assertTrue(result.provisioning_uri.startswith("otpauth://totp/"))
        self.assertIn("JobOffer%20Scanner", result.provisioning_uri)
        self.assertIn(result.secret, result.provisioning_uri)

    def test_start_setup_idempotent_before_confirm(self):
        """Re-calling start_setup should rotate the secret, not error."""
        from services.totp import is_enabled, pending_setup, start_setup

        r1 = start_setup(self.user.id, "u@x.com")
        r2 = start_setup(self.user.id, "u@x.com")
        self.assertNotEqual(r1.secret, r2.secret)
        self.assertTrue(pending_setup(self.user.id))
        self.assertFalse(is_enabled(self.user.id))

    def test_start_setup_after_enable_is_rejected(self):
        from services.totp import TOTPError, confirm_setup, start_setup

        r = start_setup(self.user.id, "u@x.com")
        confirm_setup(self.user.id, _current_otp(r.secret))
        with self.assertRaises(TOTPError):
            start_setup(self.user.id, "u@x.com")

    def test_confirm_setup_with_valid_otp_enables_and_returns_backup_codes(self):
        from services.totp import (
            confirm_setup,
            is_enabled,
            remaining_backup_codes,
            start_setup,
        )

        r = start_setup(self.user.id, "u@x.com")
        result = confirm_setup(self.user.id, _current_otp(r.secret))
        self.assertEqual(len(result.backup_codes), 10)
        # Each backup code is the user-friendly XXXXX-XXXXX format.
        for code in result.backup_codes:
            self.assertEqual(len(code), 11)
            self.assertEqual(code[5], "-")
        self.assertTrue(is_enabled(self.user.id))
        self.assertEqual(remaining_backup_codes(self.user.id), 10)

    def test_confirm_with_bad_otp_does_not_enable(self):
        from services.totp import TOTPError, confirm_setup, is_enabled, start_setup

        start_setup(self.user.id, "u@x.com")
        with self.assertRaises(TOTPError):
            confirm_setup(self.user.id, "000000")
        self.assertFalse(is_enabled(self.user.id))

    def test_confirm_without_setup_is_rejected(self):
        from services.totp import TOTPError, confirm_setup

        with self.assertRaises(TOTPError):
            confirm_setup(self.user.id, "123456")

    def test_confirm_twice_is_rejected(self):
        from services.totp import TOTPError, confirm_setup, start_setup

        r = start_setup(self.user.id, "u@x.com")
        confirm_setup(self.user.id, _current_otp(r.secret))
        with self.assertRaises(TOTPError):
            confirm_setup(self.user.id, _current_otp(r.secret))

    def test_secret_persisted_as_base32(self):
        """Stored secret must be the same one pyotp can generate codes from."""
        from db.models import UserTwoFactor
        from db.session import get_session
        from services.totp import start_setup
        from sqlalchemy import select

        r = start_setup(self.user.id, "u@x.com")
        with get_session() as s:
            row = s.execute(
                select(UserTwoFactor).where(UserTwoFactor.user_id == self.user.id)
            ).scalar_one()
            self.assertEqual(row.secret, r.secret)
            self.assertFalse(row.verified)


# ---------------------------------------------------------------------------
# Login challenge
# ---------------------------------------------------------------------------

class LoginChallengeTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        self.user = _register()
        from services.totp import confirm_setup, start_setup
        r = start_setup(self.user.id, "u@x.com")
        self.secret = r.secret
        self.backup_codes = confirm_setup(
            self.user.id, _current_otp(r.secret),
        ).backup_codes

    def test_valid_otp_succeeds(self):
        from services.totp import verify_login

        self.assertTrue(verify_login(self.user.id, _current_otp(self.secret)))

    def test_invalid_otp_returns_false(self):
        from services.totp import verify_login

        self.assertFalse(verify_login(self.user.id, "000000"))

    def test_backup_code_succeeds_and_is_consumed(self):
        from services.totp import remaining_backup_codes, verify_login

        code = self.backup_codes[0]
        self.assertTrue(verify_login(self.user.id, code))
        self.assertEqual(remaining_backup_codes(self.user.id), 9)
        # Re-using the same backup code now fails.
        self.assertFalse(verify_login(self.user.id, code))

    def test_backup_code_accepts_lowercase_and_whitespace(self):
        from services.totp import verify_login

        code = self.backup_codes[0]
        # User typed code in lowercase with extra space and dash.
        munged = " " + code.lower().replace("-", " - ") + " "
        self.assertTrue(verify_login(self.user.id, munged))

    def test_verify_login_with_no_setup_returns_false(self):
        from services.totp import verify_login

        other = _register("other@x.com")
        self.assertFalse(verify_login(other.id, "123456"))

    def test_verify_login_rate_limited(self):
        from services.rate_limit import RateLimitExceeded
        from services.totp import verify_login

        # The limiter caps verify attempts at 10/5min by default.
        for _ in range(10):
            verify_login(self.user.id, "000000")  # all fail
        with self.assertRaises(RateLimitExceeded):
            verify_login(self.user.id, "000000")

    def test_successful_verify_resets_rate_limit(self):
        from services.totp import verify_login

        for _ in range(5):
            verify_login(self.user.id, "000000")
        # Successful login clears the counter; we can fail 10 more times.
        self.assertTrue(verify_login(self.user.id, _current_otp(self.secret)))
        for _ in range(10):
            verify_login(self.user.id, "000000")


# ---------------------------------------------------------------------------
# Disable
# ---------------------------------------------------------------------------

class DisableTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        self.user = _register()
        from services.totp import confirm_setup, start_setup
        r = start_setup(self.user.id, "u@x.com")
        confirm_setup(self.user.id, _current_otp(r.secret))

    def test_disable_requires_current_password(self):
        from services.totp import TOTPError, disable, is_enabled

        with self.assertRaises(TOTPError):
            disable(self.user.id, "wrongpassword")
        self.assertTrue(is_enabled(self.user.id))

    def test_disable_succeeds_with_correct_password(self):
        from services.totp import disable, is_enabled

        disable(self.user.id, "longenough")
        self.assertFalse(is_enabled(self.user.id))

    def test_disabled_user_can_re_enroll(self):
        """After disable, start_setup should work again from scratch."""
        from services.totp import (
            confirm_setup,
            disable,
            is_enabled,
            start_setup,
        )

        disable(self.user.id, "longenough")
        r = start_setup(self.user.id, "u@x.com")
        confirm_setup(self.user.id, _current_otp(r.secret))
        self.assertTrue(is_enabled(self.user.id))


# ---------------------------------------------------------------------------
# Login flow integration with services.auth
# ---------------------------------------------------------------------------

class AuthIntegrationTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        self.user = _register()

    def test_login_without_2fa_returns_no_challenge(self):
        from services.auth import authenticate_user

        result = authenticate_user("u@x.com", "longenough")
        self.assertFalse(result.two_factor_required)

    def test_login_with_2fa_enabled_signals_challenge(self):
        from services.auth import authenticate_user
        from services.totp import confirm_setup, start_setup

        r = start_setup(self.user.id, "u@x.com")
        confirm_setup(self.user.id, _current_otp(r.secret))
        result = authenticate_user("u@x.com", "longenough")
        self.assertTrue(result.two_factor_required)
        # The id + email still come back so the UI can prompt for OTP.
        self.assertEqual(result.id, self.user.id)
        self.assertEqual(result.email, "u@x.com")


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

class AuditTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        self.user = _register()

    def test_enable_records_audit_event(self):
        from services.audit import list_for_user
        from services.totp import confirm_setup, start_setup

        r = start_setup(self.user.id, "u@x.com")
        confirm_setup(self.user.id, _current_otp(r.secret))
        kinds = {e.kind for e in list_for_user(self.user.id)}
        self.assertIn("user.2fa.enable", kinds)

    def test_verify_failure_records_audit_event(self):
        from services.audit import list_for_user
        from services.totp import confirm_setup, start_setup, verify_login

        r = start_setup(self.user.id, "u@x.com")
        confirm_setup(self.user.id, _current_otp(r.secret))
        verify_login(self.user.id, "000000")
        kinds = {e.kind for e in list_for_user(self.user.id)}
        self.assertIn("user.2fa.verify.failure", kinds)

    def test_backup_code_use_records_audit_event(self):
        from services.audit import list_for_user
        from services.totp import confirm_setup, start_setup, verify_login

        r = start_setup(self.user.id, "u@x.com")
        backup_codes = confirm_setup(self.user.id, _current_otp(r.secret)).backup_codes
        verify_login(self.user.id, backup_codes[0])
        events = list_for_user(self.user.id)
        match = next((e for e in events if e.kind == "user.2fa.backup_code.used"), None)
        self.assertIsNotNone(match)
        self.assertEqual(match.details.get("remaining"), 9)


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

class Phase20MigrationTests(unittest.TestCase):
    def test_user_two_factor_table_created(self):
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
            self.assertIn("user_two_factor", tables)


if __name__ == "__main__":
    unittest.main()
