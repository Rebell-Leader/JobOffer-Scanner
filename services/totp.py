"""TOTP-based two-factor authentication.

Setup ceremony (web side):

  1. ``start_setup(user_id, account_label)`` — generates a fresh secret,
     persists an *unverified* ``UserTwoFactor`` row, and returns the secret
     plus the ``otpauth://`` provisioning URI for the user's authenticator
     app. Calling ``start_setup`` again before confirmation overwrites the
     pending secret (no orphan setups).
  2. User scans the QR / enters the secret in their authenticator.
  3. ``confirm_setup(user_id, otp_code)`` — verifies the user's first OTP
     and atomically marks the row ``verified=True`` while generating one-
     shot backup codes. The plaintext codes are returned ONCE; only the
     bcrypt hashes are persisted.

Login challenge:

  ``verify_login(user_id, code)`` — accepts either a 6-digit OTP or one of
  the user's backup codes. Backup codes are consumed on use (the matched
  hash is removed from the list). Failures are audited individually so a
  brute-force attempt is visible.

Disable:

  ``disable(user_id, current_password)`` — wipes the row, gated by the
  user's current password. We re-use ``authenticate_user``'s constant-time
  password check via a thin wrapper to avoid duplicating the bcrypt logic.

TOTP parameters: SHA-1 (the RFC-default), 30s step, 6 digits, ±1-step skew
window (so the user's clock can drift by up to ~30s either way).
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from typing import Tuple

import bcrypt
import pyotp
from sqlalchemy import select

from db.models import User, UserTwoFactor
from db.session import get_session
from services.audit import record as _audit
from services.rate_limit import RateLimiter, RateLimitExceeded
from utils import crypto

logger = logging.getLogger(__name__)


class TOTPError(ValueError):
    """User-facing 2FA failure (bad code, no setup in progress, etc.)."""


# Same shape as the auth limiters in services/rate_limit — used here to make
# brute-forcing the 6-digit code expensive.
_VERIFY_LIMITER = RateLimiter("totp_verify", max_attempts=10, window_seconds=300)


# How many backup codes to issue at setup time. 10 is the industry default
# (GitHub, Google) — enough to survive losing the phone, few enough to keep
# the printout manageable.
_BACKUP_CODES_COUNT = 10
_BACKUP_CODE_BYTES = 6  # ~10 base32 chars after token_urlsafe; readable + strong


@dataclass(frozen=True)
class SetupResult:
    secret: str
    provisioning_uri: str


@dataclass(frozen=True)
class ConfirmResult:
    """Returned once. The plaintext ``backup_codes`` are shown to the user a
    single time — only their bcrypt hashes are persisted."""

    backup_codes: Tuple[str, ...]


# ---------------------------------------------------------------------------
# State checks
# ---------------------------------------------------------------------------

def is_enabled(user_id: int) -> bool:
    """Whether 2FA is enabled and confirmed for this user."""
    with get_session() as session:
        row = session.execute(
            select(UserTwoFactor).where(UserTwoFactor.user_id == user_id)
        ).scalar_one_or_none()
        return bool(row and row.verified)


def pending_setup(user_id: int) -> bool:
    """A start_setup row exists but the user hasn't completed confirmation yet."""
    with get_session() as session:
        row = session.execute(
            select(UserTwoFactor).where(UserTwoFactor.user_id == user_id)
        ).scalar_one_or_none()
        return bool(row and not row.verified)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def start_setup(user_id: int, account_label: str) -> SetupResult:
    """Generate a fresh TOTP secret + provisioning URI. Idempotent."""
    secret = pyotp.random_base32()
    stored = crypto.encrypt(secret)  # envelope-encrypted at rest when keyed
    with get_session() as session:
        row = session.execute(
            select(UserTwoFactor).where(UserTwoFactor.user_id == user_id)
        ).scalar_one_or_none()
        if row is None:
            row = UserTwoFactor(user_id=user_id, secret=stored, verified=False)
            session.add(row)
        else:
            # Unverified pending setup -> replace the secret. A verified row
            # would mean the user is already on 2FA and wants to re-set it;
            # disable() should be called first, so we reject loudly here.
            if row.verified:
                raise TOTPError(
                    "2FA is already enabled. Disable it first if you want a fresh setup."
                )
            row.secret = stored
        session.commit()
    issuer = "JobOffer Scanner"
    uri = pyotp.totp.TOTP(secret).provisioning_uri(name=account_label, issuer_name=issuer)
    return SetupResult(secret=secret, provisioning_uri=uri)


def confirm_setup(user_id: int, otp_code: str) -> ConfirmResult:
    """Verify the user's first code and finalise the setup.

    Generates 10 fresh backup codes, persists their bcrypt hashes, and
    returns the plaintext codes for one-time display. Audits the enable.
    """
    code = _normalize_code(otp_code)
    with get_session() as session:
        row = session.execute(
            select(UserTwoFactor).where(UserTwoFactor.user_id == user_id)
        ).scalar_one_or_none()
        if row is None:
            raise TOTPError("Start 2FA setup before confirming.")
        if row.verified:
            raise TOTPError("2FA is already enabled.")
        if not _verify_totp(row.secret, code):
            raise TOTPError("That code didn't match. Try again with a fresh OTP.")

        # Generate canonical codes; hash those; show the user the formatted
        # versions. ``verify_login`` normalises user input back to canonical
        # before bcrypt-comparing, so dashes / spaces / case don't matter on
        # input but the storage form is always canonical.
        canonicals = tuple(_generate_canonical_backup_code() for _ in range(_BACKUP_CODES_COUNT))
        row.backup_codes = [_hash_code(c) for c in canonicals]
        row.verified = True
        session.commit()

    _audit("user.2fa.enable", user_id=user_id)
    return ConfirmResult(backup_codes=tuple(_format_backup_code(c) for c in canonicals))


# ---------------------------------------------------------------------------
# Login challenge
# ---------------------------------------------------------------------------

def verify_login(user_id: int, code: str) -> bool:
    """Return True if ``code`` is a valid OTP or backup code.

    Successful login resets the rate-limit counter; failure increments it
    and audits. Backup codes are consumed on use (the matched hash is
    removed from the row's list).
    """
    code = _normalize_code(code)
    decision = _VERIFY_LIMITER.check(str(user_id))
    if not decision.allowed:
        raise RateLimitExceeded(decision.retry_after)

    with get_session() as session:
        row = session.execute(
            select(UserTwoFactor).where(UserTwoFactor.user_id == user_id)
        ).scalar_one_or_none()
        if row is None or not row.verified:
            _audit("user.2fa.verify.failure", user_id=user_id,
                   details={"reason": "no setup"})
            return False

        if _verify_totp(row.secret, code):
            # Opportunistically migrate a legacy plaintext secret to ciphertext
            # now that we've proven the key works against it.
            if crypto.encryption_enabled() and not crypto.is_encrypted(row.secret):
                row.secret = crypto.encrypt(crypto.decrypt(row.secret))
                session.commit()
            _VERIFY_LIMITER.reset(str(user_id))
            _audit("user.2fa.verify.success", user_id=user_id)
            return True

        # Backup code path.
        remaining = list(row.backup_codes or [])
        for idx, hashed in enumerate(remaining):
            if bcrypt.checkpw(code.encode("utf-8"), hashed.encode("utf-8")):
                # Consume.
                remaining.pop(idx)
                row.backup_codes = remaining
                session.commit()
                _VERIFY_LIMITER.reset(str(user_id))
                _audit("user.2fa.backup_code.used", user_id=user_id,
                       details={"remaining": len(remaining)})
                return True

        _audit("user.2fa.verify.failure", user_id=user_id)
        return False


# ---------------------------------------------------------------------------
# Disable
# ---------------------------------------------------------------------------

def disable(user_id: int, current_password: str) -> None:
    """Disable 2FA for ``user_id`` after confirming the current password."""
    with get_session() as session:
        user = session.get(User, user_id)
        if user is None or not _verify_password(current_password, user.password_hash):
            raise TOTPError("Current password is incorrect.")
        row = session.execute(
            select(UserTwoFactor).where(UserTwoFactor.user_id == user_id)
        ).scalar_one_or_none()
        if row is not None:
            session.delete(row)
            session.commit()
    _audit("user.2fa.disable", user_id=user_id)


def remaining_backup_codes(user_id: int) -> int:
    """How many one-shot backup codes the user has left."""
    with get_session() as session:
        row = session.execute(
            select(UserTwoFactor).where(UserTwoFactor.user_id == user_id)
        ).scalar_one_or_none()
        if row is None:
            return 0
        return len(row.backup_codes or [])


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalize_code(code: str) -> str:
    """Strip whitespace / dashes that authenticator apps display for clarity."""
    return (code or "").replace(" ", "").replace("-", "").strip().upper()


def _verify_totp(secret: str, code: str) -> bool:
    """RFC-default TOTP verify with ±1-step (~30s) skew tolerance.

    ``secret`` is the stored value, which may be envelope-encrypted
    (``enc:v1:…``) or legacy plaintext; ``crypto.decrypt`` handles both.
    """
    if not code.isdigit() or len(code) != 6:
        return False
    try:
        plain = crypto.decrypt(secret)
    except crypto.DecryptionError:
        logger.error("TOTP secret could not be decrypted; check SECRETS_ENCRYPTION_KEY.")
        return False
    return pyotp.TOTP(plain).verify(code, valid_window=1)


def _generate_canonical_backup_code() -> str:
    """10 uppercase alphanumeric chars — the form we hash and check against."""
    raw = secrets.token_urlsafe(_BACKUP_CODE_BYTES)
    cleaned = "".join(c for c in raw.upper() if c.isalnum())[:10]
    while len(cleaned) < 10:
        cleaned += secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")
    return cleaned


def _format_backup_code(canonical: str) -> str:
    """Pretty XXXXX-XXXXX form shown to the user for memorisation."""
    return f"{canonical[:5]}-{canonical[5:]}"


def _hash_code(code: str) -> str:
    """Hash the CANONICAL (no-dashes, uppercase) form so verification — which
    normalises user input the same way — matches what's stored."""
    return bcrypt.hashpw(code.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False
