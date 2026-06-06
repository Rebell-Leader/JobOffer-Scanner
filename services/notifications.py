"""Higher-level notifications built on the email service."""

from __future__ import annotations

import os

from services.email import send_email


def _reset_link_or_token(token: str) -> str:
    """Render the reset instruction — a full link if APP_BASE_URL is set, else
    the raw token to paste into the 'Use reset token' tab."""
    base = os.getenv("APP_BASE_URL")
    if base:
        return f"{base.rstrip('/')}/?reset_token={token}"
    return f"Reset token: {token}"


def send_password_reset_email(email: str, token: str) -> bool:
    """Email a password-reset token/link. Best-effort (False if no SMTP)."""
    instruction = _reset_link_or_token(token)
    body = (
        "We received a request to reset your JobOffer Scanner password.\n\n"
        f"{instruction}\n\n"
        "This token expires in 1 hour. If you didn't request this, ignore "
        "this email — your password is unchanged.\n"
    )
    return send_email(email, "Reset your JobOffer Scanner password", body)
