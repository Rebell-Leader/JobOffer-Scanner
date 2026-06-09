"""Higher-level notifications built on the email + Telegram services."""

from __future__ import annotations

import logging
import os

from services.email import send_email
from services.telegram_link import get_link, send_to_user

logger = logging.getLogger(__name__)


def _reset_link_or_token(token: str) -> str:
    """Render the reset instruction — a full link if APP_BASE_URL is set, else
    the raw token to paste into the 'Use reset token' tab."""
    base = os.getenv("APP_BASE_URL")
    if base:
        return f"{base.rstrip('/')}/?reset_token={token}"
    return f"Reset token: {token}"


def send_verification_email(email: str, token: str) -> bool:
    """Email an email-verification token/link. Best-effort (False if no SMTP)."""
    base = os.getenv("APP_BASE_URL")
    instruction = (
        f"{base.rstrip('/')}/?verify_token={token}" if base
        else f"Verification token: {token}"
    )
    body = (
        "Welcome to JobOffer Scanner! Confirm your email to finish setting up "
        "your account.\n\n"
        f"{instruction}\n\n"
        "This link expires in 24 hours. If you didn't sign up, ignore this email.\n"
    )
    return send_email(email, "Verify your JobOffer Scanner email", body)


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


# ---------------------------------------------------------------------------
# Stage events → Telegram
# ---------------------------------------------------------------------------

# Per-kind emoji + verbose label so the bot message reads naturally.
_STAGE_LABELS = {
    "applied": ("📤", "Applied"),
    "recruiter_screen": ("📞", "Recruiter screen"),
    "phone_screen": ("📞", "Phone screen"),
    "technical_interview": ("💻", "Technical interview"),
    "take_home": ("📝", "Take-home"),
    "onsite": ("🏢", "Onsite"),
    "offer_received": ("🎯", "Offer received"),
    "offer_accepted": ("✅", "Offer accepted"),
    "rejected": ("❌", "Rejected"),
    "withdrew": ("🛑", "Withdrew"),
    "ghosted": ("👻", "Ghosted"),
}


def notify_stage_added(user_id: int, application, stage) -> bool:
    """Send a short Telegram notification when a new stage event lands.

    Best-effort: returns False (and never raises) when the user has no
    Telegram link, has notifications disabled, the bot token is unset, or
    the Telegram API call fails. Designed to be called from the web UI
    handler after a successful add_stage — never blocks the user flow.
    """
    link = get_link(user_id)
    if link is None or not link.notify_on_stage:
        return False

    emoji, label = _STAGE_LABELS.get(stage.kind, ("•", stage.kind))
    title = getattr(application, "job_title", "?") or "?"
    company = getattr(application, "company_name", "?") or "?"
    date_str = stage.occurred_on.isoformat() if getattr(stage, "occurred_on", None) else "today"

    body_lines = [
        f"{emoji} *{label}* — {title} @ {company}",
        f"_{date_str}_",
    ]
    if getattr(stage, "notes", None):
        # Truncate to keep the notification glanceable.
        note = stage.notes.strip()
        if len(note) > 240:
            note = note[:237] + "…"
        body_lines.append("")
        body_lines.append(note)

    try:
        return send_to_user(user_id, "\n".join(body_lines))
    except Exception as exc:  # noqa: BLE001 - never bubble into the UI flow
        logger.warning("Failed to send stage notification: %s", exc)
        return False
