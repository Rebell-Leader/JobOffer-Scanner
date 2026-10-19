"""Personal mode: one local owner, no accounts.

With ``PERSONAL_MODE=1`` (``scripts/run_local.sh`` sets it) the web UI skips
sign-up / login / 2FA / billing / tokens / webhooks / Telegram and runs as a
single local owner account, created on first start. Quotas and rate limits
don't apply: it's your machine and your API key. Which subsystems are off is
defined in ``utils/features.py``.

The owner is an ordinary ``users`` row, so every service keeps its normal
ownership checks, and switching personal mode off later keeps your data.
Set PERSONAL_OWNER_EMAIL first if you want to sign in as that account then.
"""

from __future__ import annotations

import secrets
from typing import Tuple

from sqlalchemy import select

from db.models import User
from db.session import get_session
from utils.env import env_str

_DEFAULT_OWNER_EMAIL = "me@personal.local"


def owner_email() -> str:
    return (env_str("PERSONAL_OWNER_EMAIL") or _DEFAULT_OWNER_EMAIL).strip().lower()


def ensure_local_owner() -> Tuple[int, str]:
    """Return ``(user_id, email)`` of the local owner, creating it if needed.

    The password is random and never shown: in personal mode nobody logs in.
    """
    email = owner_email()
    with get_session() as session:
        user = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if user is not None:
            return user.id, user.email
    from services.auth import AuthError, register_user

    try:
        created = register_user(email, secrets.token_urlsafe(24))
        return created.id, email
    except AuthError:
        # Lost a create race with another session: read it back.
        with get_session() as session:
            user = session.execute(select(User).where(User.email == email)).scalar_one()
            return user.id, user.email
