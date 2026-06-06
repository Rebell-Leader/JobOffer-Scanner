"""Email/password authentication.

Passwords are hashed with bcrypt — never stored or logged. Email is the
canonical identifier; we normalize to lowercase so "Alice@x.com" and
"alice@x.com" can't both register.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from sqlalchemy import select

from db.models import PasswordResetToken, User
from db.session import get_session
from services.audit import record as _audit
from services.rate_limit import (
    LOGIN_LIMITER,
    REGISTER_LIMITER,
    RESET_LIMITER,
    RateLimitExceeded,
)

logger = logging.getLogger(__name__)


# Reset tokens expire fast — they're a one-shot recovery channel, not a session.
RESET_TOKEN_TTL = timedelta(hours=1)
_RESET_TOKEN_BYTES = 32  # 256 bits — safe against guessing.


# Permissive RFC-ish email check — Streamlit also has client-side validation.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MIN_PASSWORD_LEN = 8


class AuthError(ValueError):
    """User-facing auth failure (bad credentials, duplicate email, etc.)."""


@dataclass(frozen=True)
class AuthedUser:
    id: int
    email: str
    two_factor_required: bool = False


def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def register_user(email: str, password: str) -> AuthedUser:
    """Create a new user. Raises ``AuthError`` on validation / duplicate."""
    email = _normalize_email(email)
    if not _EMAIL_RE.match(email):
        raise AuthError("Please enter a valid email address.")
    if len(password) < _MIN_PASSWORD_LEN:
        raise AuthError(f"Password must be at least {_MIN_PASSWORD_LEN} characters.")

    decision = REGISTER_LIMITER.check(email)
    if not decision.allowed:
        raise RateLimitExceeded(decision.retry_after)

    with get_session() as session:
        existing = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if existing is not None:
            raise AuthError("An account with that email already exists.")
        user = User(email=email, password_hash=_hash_password(password))
        session.add(user)
        session.commit()
        session.refresh(user)
        _audit("user.register", user_id=user.id, details={"email": email})
        return AuthedUser(id=user.id, email=user.email)


def authenticate_user(email: str, password: str) -> AuthedUser:
    """Verify credentials. Raises ``AuthError`` on any failure.

    The error message is intentionally identical for "no such user" and "wrong
    password" so the response doesn't leak which emails are registered.
    """
    email = _normalize_email(email)
    decision = LOGIN_LIMITER.check(email)
    if not decision.allowed:
        raise RateLimitExceeded(decision.retry_after)
    with get_session() as session:
        user = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if user is None or not _verify_password(password, user.password_hash):
            _audit(
                "user.login.failure",
                user_id=user.id if user else None,
                details={"email": email},
            )
            raise AuthError("Invalid email or password.")
        # Clean break so a few past typos don't keep penalizing a legit login.
        LOGIN_LIMITER.reset(email)
        # Check for 2FA — return a signal but DON'T mark this as a fully
        # successful login yet (audit and rate-limit reset already happened,
        # which is correct: the password phase succeeded).
        from services.totp import is_enabled as _totp_enabled

        if _totp_enabled(user.id):
            _audit(
                "user.login.success",
                user_id=user.id,
                details={"email": email, "second_factor_pending": True},
            )
            return AuthedUser(
                id=user.id, email=user.email, two_factor_required=True,
            )
        _audit("user.login.success", user_id=user.id, details={"email": email})
        return AuthedUser(id=user.id, email=user.email)


def get_user(user_id: int) -> Optional[AuthedUser]:
    with get_session() as session:
        user = session.get(User, user_id)
        if user is None:
            return None
        return AuthedUser(id=user.id, email=user.email)


def change_password(user_id: int, current_password: str, new_password: str) -> None:
    """Change a logged-in user's password. Verifies the current password first."""
    if len(new_password) < _MIN_PASSWORD_LEN:
        raise AuthError(f"New password must be at least {_MIN_PASSWORD_LEN} characters.")
    with get_session() as session:
        user = session.get(User, user_id)
        if user is None or not _verify_password(current_password, user.password_hash):
            raise AuthError("Current password is incorrect.")
        user.password_hash = _hash_password(new_password)
        session.commit()
        _audit("user.password.change", user_id=user_id)


def request_password_reset(email: str) -> Optional[str]:
    """Generate a one-shot reset token and persist its hash.

    Returns the raw token when the user exists, else ``None``. Callers that
    surface this to end users MUST NOT reveal which outcome occurred — say
    the same thing either way ("if your email is registered…").

    Token delivery (email / SMS / display-on-screen for self-hosted) is the
    caller's responsibility — we don't bake in an email provider.
    """
    email = _normalize_email(email)
    decision = RESET_LIMITER.check(email)
    if not decision.allowed:
        raise RateLimitExceeded(decision.retry_after)
    with get_session() as session:
        user = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if user is None:
            logger.info("Password-reset requested for unknown email (no-op).")
            return None

        raw_token = secrets.token_urlsafe(_RESET_TOKEN_BYTES)
        session.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=_hash_password(raw_token),
                expires_at=datetime.utcnow() + RESET_TOKEN_TTL,
            )
        )
        session.commit()
        _audit("user.password.reset.request", user_id=user.id, details={"email": email})
        return raw_token


def complete_password_reset(email: str, token: str, new_password: str) -> None:
    """Consume a reset token and set a new password.

    The same error message covers all failure modes — wrong email, wrong
    token, expired token, already-used token — so an attacker can't probe
    which condition triggered the rejection.
    """
    if len(new_password) < _MIN_PASSWORD_LEN:
        raise AuthError(f"New password must be at least {_MIN_PASSWORD_LEN} characters.")

    generic = AuthError("Reset link is invalid or expired. Request a new one.")
    email = _normalize_email(email)

    with get_session() as session:
        user = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if user is None:
            raise generic

        candidates = session.execute(
            select(PasswordResetToken)
            .where(PasswordResetToken.user_id == user.id)
            .where(PasswordResetToken.used_at.is_(None))
            .where(PasswordResetToken.expires_at > datetime.utcnow())
        ).scalars().all()

        match = next(
            (t for t in candidates if _verify_password(token, t.token_hash)),
            None,
        )
        if match is None:
            raise generic

        match.used_at = datetime.utcnow()
        user.password_hash = _hash_password(new_password)
        session.commit()
        _audit("user.password.reset.complete", user_id=user.id)
