"""Email/password authentication.

Passwords are hashed with bcrypt — never stored or logged. Email is the
canonical identifier; we normalize to lowercase so "Alice@x.com" and
"alice@x.com" can't both register.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import bcrypt
from sqlalchemy import select

from db.models import User
from db.session import get_session


# Permissive RFC-ish email check — Streamlit also has client-side validation.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MIN_PASSWORD_LEN = 8


class AuthError(ValueError):
    """User-facing auth failure (bad credentials, duplicate email, etc.)."""


@dataclass(frozen=True)
class AuthedUser:
    id: int
    email: str


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

    with get_session() as session:
        existing = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if existing is not None:
            raise AuthError("An account with that email already exists.")
        user = User(email=email, password_hash=_hash_password(password))
        session.add(user)
        session.commit()
        session.refresh(user)
        return AuthedUser(id=user.id, email=user.email)


def authenticate_user(email: str, password: str) -> AuthedUser:
    """Verify credentials. Raises ``AuthError`` on any failure.

    The error message is intentionally identical for "no such user" and "wrong
    password" so the response doesn't leak which emails are registered.
    """
    email = _normalize_email(email)
    with get_session() as session:
        user = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if user is None or not _verify_password(password, user.password_hash):
            raise AuthError("Invalid email or password.")
        return AuthedUser(id=user.id, email=user.email)


def get_user(user_id: int) -> Optional[AuthedUser]:
    with get_session() as session:
        user = session.get(User, user_id)
        if user is None:
            return None
        return AuthedUser(id=user.id, email=user.email)
