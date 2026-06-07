"""P1 #6: envelope-encryption of secrets at rest (utils.crypto + TOTP wiring).

Covers the crypto primitives (round-trip, prefix scheme, transparent
pass-through when unkeyed, decrypt-failure policy) and the integration with
the TOTP service: the stored secret is ciphertext when keyed, login still
verifies, and a legacy plaintext secret is re-encrypted on first verify.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pyotp  # noqa: E402

from db.session import get_session, reset_engine_for_testing  # noqa: E402
from utils import crypto  # noqa: E402

_KEY = "test-secrets-encryption-key-please-ignore"


class CryptoUnitTests(unittest.TestCase):
    def setUp(self):
        crypto.reset_cache_for_testing()

    def tearDown(self):
        crypto.reset_cache_for_testing()

    def test_passthrough_when_unkeyed(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SECRETS_ENCRYPTION_KEY", None)
            crypto.reset_cache_for_testing()
            self.assertFalse(crypto.encryption_enabled())
            self.assertEqual(crypto.encrypt("ABC123"), "ABC123")
            self.assertEqual(crypto.decrypt("ABC123"), "ABC123")

    def test_round_trip_when_keyed(self):
        with mock.patch.dict(os.environ, {"SECRETS_ENCRYPTION_KEY": _KEY}):
            crypto.reset_cache_for_testing()
            self.assertTrue(crypto.encryption_enabled())
            token = crypto.encrypt("JBSWY3DPEHPK3PXP")
            self.assertTrue(token.startswith("enc:v1:"))
            self.assertNotIn("JBSWY3DPEHPK3PXP", token)
            self.assertTrue(crypto.is_encrypted(token))
            self.assertEqual(crypto.decrypt(token), "JBSWY3DPEHPK3PXP")

    def test_encrypt_is_idempotent(self):
        with mock.patch.dict(os.environ, {"SECRETS_ENCRYPTION_KEY": _KEY}):
            crypto.reset_cache_for_testing()
            once = crypto.encrypt("secret")
            twice = crypto.encrypt(once)
            self.assertEqual(once, twice)

    def test_legacy_plaintext_decrypts_transparently_when_keyed(self):
        with mock.patch.dict(os.environ, {"SECRETS_ENCRYPTION_KEY": _KEY}):
            crypto.reset_cache_for_testing()
            # A value without the prefix is treated as legacy plaintext.
            self.assertEqual(crypto.decrypt("legacy-plain"), "legacy-plain")

    def test_decrypt_prefixed_without_key_raises(self):
        with mock.patch.dict(os.environ, {"SECRETS_ENCRYPTION_KEY": _KEY}):
            crypto.reset_cache_for_testing()
            token = crypto.encrypt("secret")
        os.environ.pop("SECRETS_ENCRYPTION_KEY", None)
        crypto.reset_cache_for_testing()
        with self.assertRaises(crypto.DecryptionError):
            crypto.decrypt(token)

    def test_decrypt_wrong_key_raises(self):
        with mock.patch.dict(os.environ, {"SECRETS_ENCRYPTION_KEY": _KEY}):
            crypto.reset_cache_for_testing()
            token = crypto.encrypt("secret")
        with mock.patch.dict(os.environ, {"SECRETS_ENCRYPTION_KEY": "a-different-key"}):
            crypto.reset_cache_for_testing()
            with self.assertRaises(crypto.DecryptionError):
                crypto.decrypt(token)


class TotpEncryptionIntegrationTests(unittest.TestCase):
    def setUp(self):
        reset_engine_for_testing("sqlite:///:memory:")
        crypto.reset_cache_for_testing()
        # Create a user to attach 2FA to.
        from services import auth
        self.user = auth.register_user("enc@example.com", "Sup3rSecret!")

    def tearDown(self):
        crypto.reset_cache_for_testing()

    def _enable_2fa(self):
        from services import totp
        setup = totp.start_setup(self.user.id, "enc@example.com")
        code = pyotp.TOTP(setup.secret).now()
        totp.confirm_setup(self.user.id, code)
        return setup.secret

    def test_secret_stored_encrypted_when_keyed(self):
        from sqlalchemy import select

        from db.models import UserTwoFactor
        with mock.patch.dict(os.environ, {"SECRETS_ENCRYPTION_KEY": _KEY}):
            crypto.reset_cache_for_testing()
            plain_secret = self._enable_2fa()
            with get_session() as session:
                row = session.execute(
                    select(UserTwoFactor).where(UserTwoFactor.user_id == self.user.id)
                ).scalar_one()
                self.assertTrue(row.secret.startswith("enc:v1:"))
                self.assertNotIn(plain_secret, row.secret)

    def test_login_verifies_against_encrypted_secret(self):
        from services import totp
        with mock.patch.dict(os.environ, {"SECRETS_ENCRYPTION_KEY": _KEY}):
            crypto.reset_cache_for_testing()
            plain_secret = self._enable_2fa()
            code = pyotp.TOTP(plain_secret).now()
            self.assertTrue(totp.verify_login(self.user.id, code))

    def test_legacy_plaintext_secret_reencrypted_on_verify(self):
        from sqlalchemy import select

        from db.models import UserTwoFactor
        from services import totp

        # Enable WITHOUT a key -> plaintext at rest (legacy state).
        plain_secret = self._enable_2fa()
        with get_session() as session:
            row = session.execute(
                select(UserTwoFactor).where(UserTwoFactor.user_id == self.user.id)
            ).scalar_one()
            self.assertFalse(row.secret.startswith("enc:v1:"))

        # Now a key appears; a successful verify should migrate it to ciphertext.
        with mock.patch.dict(os.environ, {"SECRETS_ENCRYPTION_KEY": _KEY}):
            crypto.reset_cache_for_testing()
            code = pyotp.TOTP(plain_secret).now()
            self.assertTrue(totp.verify_login(self.user.id, code))
            with get_session() as session:
                row = session.execute(
                    select(UserTwoFactor).where(UserTwoFactor.user_id == self.user.id)
                ).scalar_one()
                self.assertTrue(row.secret.startswith("enc:v1:"))
                self.assertEqual(crypto.decrypt(row.secret), plain_secret)


if __name__ == "__main__":
    unittest.main()
