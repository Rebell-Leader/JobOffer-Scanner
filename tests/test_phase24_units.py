"""Unit tests for Phase 24: OAuth login (Google / GitHub)."""

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


_GOOGLE_ENV = {
    "GOOGLE_OAUTH_CLIENT_ID": "gid",
    "GOOGLE_OAUTH_CLIENT_SECRET": "gsecret",
    "OAUTH_REDIRECT_URI": "https://app.test/",
}


# ---------------------------------------------------------------------------
# Config + URL building
# ---------------------------------------------------------------------------

class ConfigTests(unittest.TestCase):
    def setUp(self):
        for k in ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET",
                  "GITHUB_OAUTH_CLIENT_ID", "GITHUB_OAUTH_CLIENT_SECRET",
                  "OAUTH_REDIRECT_URI"):
            os.environ.pop(k, None)

    def test_unconfigured_provider(self):
        from services.oauth import configured_providers, is_configured

        self.assertFalse(is_configured("google"))
        self.assertEqual(configured_providers(), [])

    def test_configured_when_env_present(self):
        from services.oauth import configured_providers, is_configured

        with mock.patch.dict(os.environ, _GOOGLE_ENV):
            self.assertTrue(is_configured("google"))
            self.assertIn("google", configured_providers())
            self.assertNotIn("github", configured_providers())

    def test_unknown_provider_is_not_configured(self):
        from services.oauth import is_configured

        self.assertFalse(is_configured("facebook"))

    def test_build_authorize_url_contains_required_params(self):
        from services.oauth import build_authorize_url

        with mock.patch.dict(os.environ, _GOOGLE_ENV):
            url = build_authorize_url("google", "statenonce")
        self.assertIn("accounts.google.com", url)
        self.assertIn("client_id=gid", url)
        self.assertIn("state=statenonce", url)
        self.assertIn("response_type=code", url)
        self.assertIn("redirect_uri=", url)

    def test_build_authorize_url_unconfigured_raises(self):
        from services.oauth import OAuthError, build_authorize_url

        with self.assertRaises(OAuthError):
            build_authorize_url("google", "x")


# ---------------------------------------------------------------------------
# Identity resolution (login / link / register)
# ---------------------------------------------------------------------------

class ResolveIdentityTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()

    def test_new_identity_creates_user_and_links(self):
        from sqlalchemy import select

        from db.models import OAuthIdentity, User
        from db.session import get_session
        from services.oauth import resolve_identity

        user = resolve_identity("google", "sub-123", "new@example.com")
        self.assertEqual(user.email, "new@example.com")
        with get_session() as s:
            users = s.execute(select(User)).scalars().all()
            self.assertEqual(len(users), 1)
            ident = s.execute(select(OAuthIdentity)).scalar_one()
            self.assertEqual(ident.provider, "google")
            self.assertEqual(ident.provider_user_id, "sub-123")
            self.assertEqual(ident.user_id, user.id)

    def test_existing_identity_logs_in_same_user(self):
        from services.oauth import resolve_identity

        u1 = resolve_identity("google", "sub-123", "a@example.com")
        u2 = resolve_identity("google", "sub-123", "a@example.com")
        self.assertEqual(u1.id, u2.id)

    def test_email_match_links_new_identity_to_existing_user(self):
        from sqlalchemy import select

        from db.models import OAuthIdentity
        from db.session import get_session
        from services.auth import register_user
        from services.oauth import resolve_identity

        existing = register_user("shared@example.com", "longenough")
        # OAuth login with a DIFFERENT provider id but the same VERIFIED email links.
        resolved = resolve_identity("github", "gh-999", "shared@example.com",
                                    email_verified=True)
        self.assertEqual(resolved.id, existing.id)
        with get_session() as s:
            idents = s.execute(
                select(OAuthIdentity).where(OAuthIdentity.user_id == existing.id)
            ).scalars().all()
            self.assertEqual(len(idents), 1)
            self.assertEqual(idents[0].provider, "github")

    def test_unverified_email_refuses_link_to_existing_account(self):
        # Account-takeover guard: an unverified provider email must NOT auto-link
        # to a pre-existing local account.
        from services.auth import register_user
        from services.oauth import OAuthError, resolve_identity

        register_user("victim@example.com", "longenough")
        with self.assertRaises(OAuthError):
            resolve_identity("github", "attacker-1", "victim@example.com",
                             email_verified=False)

    def test_two_providers_same_email_resolve_same_account(self):
        from services.oauth import resolve_identity

        g = resolve_identity("google", "g-1", "same@example.com", email_verified=True)
        h = resolve_identity("github", "h-1", "same@example.com", email_verified=True)
        self.assertEqual(g.id, h.id)

    def test_missing_email_for_new_identity_raises(self):
        from services.oauth import OAuthError, resolve_identity

        with self.assertRaises(OAuthError):
            resolve_identity("google", "sub-x", None)

    def test_missing_provider_user_id_raises(self):
        from services.oauth import OAuthError, resolve_identity

        with self.assertRaises(OAuthError):
            resolve_identity("google", "", "a@example.com")

    def test_audit_events_recorded(self):
        from services.audit import list_for_user
        from services.oauth import resolve_identity

        user = resolve_identity("google", "sub-1", "a@example.com")  # register
        resolve_identity("google", "sub-1", "a@example.com")          # login
        kinds = {e.kind for e in list_for_user(user.id)}
        self.assertIn("user.oauth.register", kinds)
        self.assertIn("user.oauth.login", kinds)


# ---------------------------------------------------------------------------
# complete_login orchestration (network mocked)
# ---------------------------------------------------------------------------

class CompleteLoginTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()

    def test_complete_login_google_end_to_end(self):
        import services.oauth as mod

        with mock.patch.dict(os.environ, _GOOGLE_ENV), \
             mock.patch.object(mod, "_exchange_code", return_value="tok"), \
             mock.patch.object(mod, "_fetch_userinfo",
                               return_value={"provider_user_id": "sub-42",
                                             "email": "real@example.com"}):
            user = mod.complete_login("google", "auth-code")
        self.assertEqual(user.email, "real@example.com")

    def test_complete_login_unconfigured_raises(self):
        import services.oauth as mod

        for k in list(_GOOGLE_ENV):
            os.environ.pop(k, None)
        with self.assertRaises(mod.OAuthError):
            mod.complete_login("google", "code")

    def test_complete_login_empty_code_raises(self):
        import services.oauth as mod

        with mock.patch.dict(os.environ, _GOOGLE_ENV):
            with self.assertRaises(mod.OAuthError):
                mod.complete_login("google", "")

    def test_network_failure_wrapped_in_oautherror(self):
        import services.oauth as mod

        with mock.patch.dict(os.environ, _GOOGLE_ENV), \
             mock.patch.object(mod, "_exchange_code",
                               side_effect=RuntimeError("boom")):
            with self.assertRaises(mod.OAuthError):
                mod.complete_login("google", "code")


# ---------------------------------------------------------------------------
# userinfo normalisation
# ---------------------------------------------------------------------------

class UserinfoNormaliseTests(unittest.TestCase):
    def test_google_userinfo_shape(self):
        import services.oauth as mod

        cfg = mod.PROVIDERS["google"]
        with mock.patch.object(mod.requests, "get") as get:
            get.return_value = mock.Mock(
                status_code=200,
                json=lambda: {"sub": "12345", "email": "g@x.com", "email_verified": True},
                raise_for_status=lambda: None,
            )
            info = mod._fetch_userinfo(cfg, "tok")
        self.assertEqual(info, {"provider_user_id": "12345", "email": "g@x.com",
                                "email_verified": True})

    def test_google_unverified_email_marked_unverified(self):
        import services.oauth as mod

        cfg = mod.PROVIDERS["google"]
        with mock.patch.object(mod.requests, "get") as get:
            get.return_value = mock.Mock(
                status_code=200,
                json=lambda: {"sub": "1", "email": "g@x.com", "email_verified": False},
                raise_for_status=lambda: None,
            )
            info = mod._fetch_userinfo(cfg, "tok")
        self.assertFalse(info["email_verified"])

    def test_github_inline_email_is_unverified(self):
        import services.oauth as mod

        cfg = mod.PROVIDERS["github"]
        # No verified primary -> the public profile email is accepted but
        # marked unverified (GitHub /user.email carries no verified flag).
        with mock.patch.object(mod.requests, "get") as get, \
             mock.patch.object(mod, "_github_verified_primary_email", return_value=None):
            get.return_value = mock.Mock(
                status_code=200,
                json=lambda: {"id": 777, "email": "gh@x.com"},
                raise_for_status=lambda: None,
            )
            info = mod._fetch_userinfo(cfg, "tok")
        self.assertEqual(info, {"provider_user_id": "777", "email": "gh@x.com",
                                "email_verified": False})

    def test_github_verified_primary_is_trusted(self):
        import services.oauth as mod

        cfg = mod.PROVIDERS["github"]
        with mock.patch.object(mod.requests, "get") as get, \
             mock.patch.object(mod, "_github_verified_primary_email",
                               return_value="primary@x.com"):
            get.return_value = mock.Mock(
                status_code=200,
                json=lambda: {"id": 5, "email": None},
                raise_for_status=lambda: None,
            )
            info = mod._fetch_userinfo(cfg, "tok")
        self.assertEqual(info["email"], "primary@x.com")
        self.assertTrue(info["email_verified"])

    def test_github_verified_primary_filters_unverified(self):
        import services.oauth as mod

        # _github_verified_primary_email returns ONLY a verified primary.
        with mock.patch.object(mod.requests, "get") as get:
            get.return_value = mock.Mock(
                status_code=200,
                json=lambda: [
                    {"email": "unverified@x.com", "primary": True, "verified": False},
                    {"email": "other@x.com", "primary": False, "verified": True},
                ],
                raise_for_status=lambda: None,
            )
            self.assertIsNone(mod._github_verified_primary_email("tok"))


# ---------------------------------------------------------------------------
# auth helpers
# ---------------------------------------------------------------------------

class AuthHelperTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()

    def test_create_oauth_user_has_unusable_password(self):
        from services.auth import AuthError, authenticate_user, create_oauth_user

        u = create_oauth_user("oauth@x.com")
        self.assertEqual(u.email, "oauth@x.com")
        # The random password is unknowable — a blank/guess login fails.
        with self.assertRaises(AuthError):
            authenticate_user("oauth@x.com", "password")

    def test_create_oauth_user_idempotent_on_existing_email(self):
        from services.auth import create_oauth_user, register_user

        existing = register_user("dup@x.com", "longenough")
        again = create_oauth_user("dup@x.com")
        self.assertEqual(again.id, existing.id)

    def test_find_user_by_email(self):
        from services.auth import find_user_by_email, register_user

        register_user("findme@x.com", "longenough")
        self.assertIsNotNone(find_user_by_email("FINDME@x.com"))  # case-insensitive
        self.assertIsNone(find_user_by_email("nobody@x.com"))


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

class Phase24MigrationTests(unittest.TestCase):
    def test_table_created(self):
        from sqlalchemy import create_engine, inspect

        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "alembic.db"
            env = dict(os.environ)
            env["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
            result = subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                cwd=project_root, env=env, capture_output=True, text=True,
            )
            self.assertEqual(
                result.returncode, 0,
                msg=f"alembic upgrade failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}",
            )
            tables = set(inspect(create_engine(f"sqlite:///{db_path.as_posix()}")).get_table_names())
            self.assertIn("oauth_identities", tables)


if __name__ == "__main__":
    unittest.main()
