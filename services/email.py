"""Email delivery via SMTP.

Configured via env (all required for live sending):
  SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, EMAIL_FROM
  SMTP_USE_TLS=1 (default) for STARTTLS.

When SMTP isn't configured, ``send_email`` logs the message and returns False
instead of raising — callers treat email as best-effort so a missing mail
server never breaks a user flow.

The MIME builder (``build_message``) is a pure function so it's unit-testable
without a mail server.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from typing import Optional

from utils.env import env_bool, env_int

logger = logging.getLogger(__name__)


def email_configured() -> bool:
    return all(
        os.getenv(k)
        for k in ("SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD", "EMAIL_FROM")
    )


def build_message(to: str, subject: str, body: str, sender: Optional[str] = None) -> EmailMessage:
    """Build a plain-text email message (pure)."""
    msg = EmailMessage()
    msg["From"] = sender or os.getenv("EMAIL_FROM", "no-reply@joboffer.local")
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    return msg


def send_email(to: str, subject: str, body: str) -> bool:
    """Send an email. Returns True on success, False if unconfigured/failed."""
    if not email_configured():
        logger.info("SMTP not configured — email to %s not sent. Subject: %s", to, subject)
        return False

    msg = build_message(to, subject, body)
    # email_configured() above guarantees these are present; the "" defaults
    # are only to satisfy the type checker (str, not str | None).
    host = os.getenv("SMTP_HOST", "")
    port = env_int("SMTP_PORT", 587)
    username = os.getenv("SMTP_USERNAME", "")
    password = os.getenv("SMTP_PASSWORD", "")
    use_tls = env_bool("SMTP_USE_TLS", True)

    try:
        with smtplib.SMTP(host, port, timeout=15) as server:
            if use_tls:
                server.starttls()
            server.login(username, password)
            server.send_message(msg)
        logger.info("Sent email to %s (subject: %s)", to, subject)
        return True
    except Exception as exc:  # noqa: BLE001 - email is best-effort
        logger.warning("Failed to send email to %s: %s", to, exc)
        return False
