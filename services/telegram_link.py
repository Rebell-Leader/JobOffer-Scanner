"""Web ↔ Telegram chat binding + outbound notification HTTP sender.

Two flows:

  1. **Binding** — the user clicks "Link Telegram" in the web UI, we hand them
     a short, opaque token. They send ``/bind <token>`` to the bot. The bot
     calls ``complete_binding(chat_id, raw_token)``; we look up an unexpired
     token row by case-insensitive bcrypt match, mark it used, and persist
     the chat_id under their account. Same single-error-message discipline
     as the password reset flow — we never reveal whether a particular token
     value was valid vs expired vs already-used.

  2. **Sending** — once linked, ``send_to_user`` POSTs to the public Telegram
     HTTP API directly (no shared state with the bot's long-running process).
     This deliberately bypasses ``python-telegram-bot`` so the web container
     can notify without needing a worker round-trip.

Both halves are best-effort: missing ``TELEGRAM_BOT_TOKEN`` or network
failures don't break the web flow that triggered the notification.
"""

from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
import requests
from sqlalchemy import select

from db.models import TelegramLink, TelegramLinkBindingToken
from db.session import get_session

logger = logging.getLogger(__name__)


# Token TTL is generous — the user needs time to switch apps.
BINDING_TOKEN_TTL = timedelta(minutes=15)
_BINDING_TOKEN_BYTES = 16

_TELEGRAM_HTTP_TIMEOUT = float(os.getenv("TELEGRAM_HTTP_TIMEOUT", "8"))
_TELEGRAM_BASE = "https://api.telegram.org"


class TelegramLinkError(ValueError):
    """User-facing failure (invalid token, ownership violation, etc.)."""


@dataclass
class LinkRecord:
    user_id: int
    chat_id: int
    chat_username: Optional[str]
    notify_on_stage: bool
    inactive_reminder_days: int
    created_at: datetime


def _to_record(link: TelegramLink) -> LinkRecord:
    return LinkRecord(
        user_id=link.user_id,
        chat_id=link.chat_id,
        chat_username=link.chat_username,
        notify_on_stage=link.notify_on_stage,
        inactive_reminder_days=link.inactive_reminder_days,
        created_at=link.created_at,
    )


def _hash(token: str) -> str:
    return bcrypt.hashpw(token.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify(token: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(token.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# Binding (web side)
# ---------------------------------------------------------------------------

def issue_binding_token(user_id: int) -> str:
    """Generate a new binding token. The user types ``/bind <token>`` in the bot.

    Previously-issued unused tokens for the same user stay valid until their
    own expiry — that's intentional, in case the user generated a token,
    switched apps, then re-opened the web view and pressed the button again.
    """
    raw = secrets.token_urlsafe(_BINDING_TOKEN_BYTES)
    with get_session() as session:
        session.add(
            TelegramLinkBindingToken(
                user_id=user_id,
                token_hash=_hash(raw),
                expires_at=datetime.utcnow() + BINDING_TOKEN_TTL,
            )
        )
        session.commit()
    return raw


def complete_binding(
    chat_id: int,
    raw_token: str,
    chat_username: Optional[str] = None,
) -> LinkRecord:
    """Bot side: consume a binding token and persist the chat link.

    Raises ``TelegramLinkError`` for all failure modes with a single generic
    message (no probing which-token-is-valid).
    """
    generic = TelegramLinkError("Invalid or expired binding token.")
    raw_token = (raw_token or "").strip()
    if not raw_token:
        raise generic

    with get_session() as session:
        candidates = session.execute(
            select(TelegramLinkBindingToken)
            .where(TelegramLinkBindingToken.used_at.is_(None))
            .where(TelegramLinkBindingToken.expires_at > datetime.utcnow())
        ).scalars().all()
        match = next(
            (t for t in candidates if _verify(raw_token, t.token_hash)),
            None,
        )
        if match is None:
            raise generic

        match.used_at = datetime.utcnow()

        # Either insert a new link or replace the existing one (one per user).
        existing = session.execute(
            select(TelegramLink).where(TelegramLink.user_id == match.user_id)
        ).scalar_one_or_none()
        if existing is None:
            link = TelegramLink(
                user_id=match.user_id,
                chat_id=chat_id,
                chat_username=chat_username,
                notify_on_stage=True,
            )
            session.add(link)
        else:
            existing.chat_id = chat_id
            existing.chat_username = chat_username
            link = existing
        session.commit()
        session.refresh(link)
        return _to_record(link)


def get_link(user_id: int) -> Optional[LinkRecord]:
    with get_session() as session:
        link = session.execute(
            select(TelegramLink).where(TelegramLink.user_id == user_id)
        ).scalar_one_or_none()
        return _to_record(link) if link else None


def get_user_id_by_chat(chat_id: int) -> Optional[int]:
    with get_session() as session:
        link = session.execute(
            select(TelegramLink).where(TelegramLink.chat_id == chat_id)
        ).scalar_one_or_none()
        return link.user_id if link else None


def unlink(user_id: int) -> bool:
    """Delete the user's link. Returns True if a row was removed."""
    with get_session() as session:
        link = session.execute(
            select(TelegramLink).where(TelegramLink.user_id == user_id)
        ).scalar_one_or_none()
        if link is None:
            return False
        session.delete(link)
        session.commit()
        return True


def set_notify_on_stage(user_id: int, enabled: bool) -> None:
    with get_session() as session:
        link = session.execute(
            select(TelegramLink).where(TelegramLink.user_id == user_id)
        ).scalar_one_or_none()
        if link is None:
            raise TelegramLinkError("Telegram not linked to this account.")
        link.notify_on_stage = bool(enabled)
        session.commit()


# ---------------------------------------------------------------------------
# Outbound send (HTTP API)
# ---------------------------------------------------------------------------

def send_to_chat(chat_id: int, text: str, parse_mode: str = "Markdown") -> bool:
    """Send ``text`` to ``chat_id`` via the public Telegram HTTP API."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.info("TELEGRAM_BOT_TOKEN unset; skipping notification.")
        return False
    url = f"{_TELEGRAM_BASE}/bot{token}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            },
            timeout=_TELEGRAM_HTTP_TIMEOUT,
        )
        if resp.status_code >= 400:
            logger.warning(
                "Telegram sendMessage failed: %s %s", resp.status_code, resp.text[:200],
            )
            return False
        return True
    except Exception as exc:  # noqa: BLE001 - best-effort
        logger.warning("Telegram sendMessage exception: %s", exc)
        return False


def send_to_user(user_id: int, text: str) -> bool:
    """Look up the user's linked chat and send ``text`` to it. Best-effort."""
    link = get_link(user_id)
    if link is None:
        return False
    return send_to_chat(link.chat_id, text)
