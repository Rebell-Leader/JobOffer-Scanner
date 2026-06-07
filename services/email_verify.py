"""Email verification — bcrypt-hashed, one-shot, expiring tokens.

Created on signup; the user verifies by pasting the token (or clicking the
emailed link). Same security shape as password reset: only the hash is
stored, a single generic error covers all failure modes, success is one-shot.

Gating is the caller's choice: ``is_verified`` lets the UI show a banner, and
the app can hard-block usage when ``REQUIRE_EMAIL_VERIFICATION=1``. OAuth
users are created already-verified (the provider verified the email), and
existing pre-feature accounts were backfilled verified by the migration.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from sqlalchemy import select

from db.models import EmailVerificationToken, User
from db.session import get_session
from services.audit import record as _audit
from services.rate_limit import RateLimiter, RateLimitExceeded

VERIFY_TOKEN_TTL = timedelta(hours=24)
_TOKEN_BYTES = 32

# Cap resend spam.
_REQUEST_LIMITER = RateLimiter("email_verify_request", max_attempts=5, window_seconds=3600)


class EmailVerifyError(ValueError):
    """User-facing failure (invalid/expired/used token)."""


def _hash(token: str) -> str:
    return bcrypt.hashpw(token.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify(token: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(token.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def is_verified(user_id: int) -> bool:
    with get_session() as session:
        user = session.get(User, user_id)
        return bool(user and user.email_verified)


def start_verification(user_id: int) -> Optional[str]:
    """Issue a verification token for the user. Returns the raw token, or None
    if the account is already verified (no-op)."""
    decision = _REQUEST_LIMITER.check(str(user_id))
    if not decision.allowed:
        raise RateLimitExceeded(decision.retry_after)

    with get_session() as session:
        user = session.get(User, user_id)
        if user is None:
            raise EmailVerifyError("Account not found.")
        if user.email_verified:
            return None
        raw = secrets.token_urlsafe(_TOKEN_BYTES)
        session.add(
            EmailVerificationToken(
                user_id=user_id,
                token_hash=_hash(raw),
                expires_at=datetime.utcnow() + VERIFY_TOKEN_TTL,
            )
        )
        session.commit()
    _audit("user.email.verify.request", user_id=user_id)
    return raw


def complete_verification(email: str, token: str) -> None:
    """Consume a verification token and mark the account verified.

    Single generic error for unknown-email / wrong / expired / used token.
    """
    generic = EmailVerifyError("Verification link is invalid or expired.")
    email = (email or "").strip().lower()
    token = (token or "").strip()
    if not token:
        raise generic

    with get_session() as session:
        user = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if user is None:
            raise generic
        if user.email_verified:
            return  # already done — idempotent success
        candidates = session.execute(
            select(EmailVerificationToken)
            .where(EmailVerificationToken.user_id == user.id)
            .where(EmailVerificationToken.used_at.is_(None))
            .where(EmailVerificationToken.expires_at > datetime.utcnow())
        ).scalars().all()
        match = next((t for t in candidates if _verify(token, t.token_hash)), None)
        if match is None:
            raise generic
        match.used_at = datetime.utcnow()
        user.email_verified = True
        session.commit()
    _audit("user.email.verify.complete", user_id=user.id)
