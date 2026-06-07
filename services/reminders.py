"""Inactivity reminders: ping the user when an application sits idle too long.

Definition of "idle":

  * The application is NOT in a terminal state (no ``rejected`` / ``withdrew``
    / ``ghosted`` / ``offer_accepted`` stage event).
  * The most-recent stage event (or the application's ``created_at`` if there
    are none) is older than the user's ``inactive_reminder_days`` threshold.
  * The application is NOT snoozed (no future ``snooze_reminders_until``).

Designed to be driven by a scheduler (cron, Celery beat, etc.) — the
``send_inactivity_reminders`` entry point is idempotent at the day boundary:
calling it twice in the same day still produces at most one Telegram message
per user, because we record ``last_inactivity_notified_on`` in the linker.
Actually we don't yet — see the explicit-flag note in the function: the
current MVP relies on the scheduler running once per day. A persisted
"notified on" flag is the next iteration if cron jitter becomes a real issue.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import timedelta
from typing import List, Optional

from sqlalchemy import asc, select

from db.models import (
    TERMINAL_NEGATIVE_STAGES,
    Application,
    ApplicationStage,
    TelegramLink,
)
from db.session import get_session
from services.telegram_link import send_to_chat

logger = logging.getLogger(__name__)

# Terminal stages that close the application — no reminders past these.
_CLOSED_STAGES = set(TERMINAL_NEGATIVE_STAGES) | {"offer_accepted"}


@dataclass
class StaleApplication:
    application_id: int
    company_name: str
    job_title: str
    last_event: date_cls
    days_idle: int


# ---------------------------------------------------------------------------
# Detection — pure read, no notification side-effects
# ---------------------------------------------------------------------------

def find_stale_applications(
    user_id: int,
    *,
    threshold_days: int,
    today: Optional[date_cls] = None,
) -> List[StaleApplication]:
    """Return all the user's applications that haven't had activity recently.

    ``threshold_days <= 0`` disables the check (returns ``[]``) so the same
    function is safe to call regardless of whether the user opted in.
    """
    if threshold_days is None or threshold_days <= 0:
        return []

    today = today or date_cls.today()
    cutoff = today - timedelta(days=threshold_days)

    out: List[StaleApplication] = []
    with get_session() as session:
        apps = session.execute(
            select(Application).where(Application.user_id == user_id)
        ).scalars().all()
        for app in apps:
            # Skip snoozed applications.
            if app.snooze_reminders_until and app.snooze_reminders_until >= today:
                continue

            stages = session.execute(
                select(ApplicationStage)
                .where(ApplicationStage.application_id == app.id)
                .order_by(asc(ApplicationStage.occurred_on),
                          asc(ApplicationStage.id))
            ).scalars().all()
            kinds = {s.kind for s in stages}
            if kinds & _CLOSED_STAGES:
                continue

            if stages:
                last_event = stages[-1].occurred_on
            else:
                # No stages — anchor at the application's creation date.
                last_event = app.created_at.date() if app.created_at else today

            if last_event > cutoff:
                continue

            out.append(StaleApplication(
                application_id=app.id,
                company_name=app.company_name,
                job_title=app.job_title,
                last_event=last_event,
                days_idle=(today - last_event).days,
            ))
    out.sort(key=lambda s: s.days_idle, reverse=True)
    return out


# ---------------------------------------------------------------------------
# Notification — Telegram one-shot summary per user
# ---------------------------------------------------------------------------

def format_summary(stale: List[StaleApplication]) -> str:
    """Render a Markdown-formatted summary suitable for Telegram."""
    if not stale:
        return ""
    lines = [
        f"🔔 *Stale applications* — {len(stale)} need an update:",
        "",
    ]
    for s in stale[:10]:
        lines.append(
            f"• *{s.job_title}* @ {s.company_name} — _{s.days_idle}d idle_ "
            f"(last event {s.last_event.isoformat()})"
        )
    if len(stale) > 10:
        lines.append(f"…and {len(stale) - 10} more.")
    lines.append("")
    lines.append("Snooze any of them from the My Applications tab.")
    return "\n".join(lines)


def send_inactivity_reminders(
    user_id: Optional[int] = None,
    today: Optional[date_cls] = None,
) -> int:
    """Detect + notify. Returns the number of users we sent to.

    ``user_id=None`` runs for every user that has a Telegram link with a
    non-zero ``inactive_reminder_days``. Pass a specific user_id to target one
    account (useful for tests / one-shot scripts).
    """
    sent = 0
    today = today or date_cls.today()
    with get_session() as session:
        q = select(TelegramLink).where(TelegramLink.inactive_reminder_days > 0)
        if user_id is not None:
            q = q.where(TelegramLink.user_id == user_id)
        links = session.execute(q).scalars().all()

    for link in links:
        try:
            stale = find_stale_applications(
                link.user_id,
                threshold_days=link.inactive_reminder_days,
                today=today,
            )
            if not stale:
                continue
            text = format_summary(stale)
            if send_to_chat(link.chat_id, text):
                sent += 1
        except Exception as exc:  # noqa: BLE001 - never break the loop on one user
            logger.warning("Reminder run for user %s failed: %s", link.user_id, exc)
    return sent


# ---------------------------------------------------------------------------
# Snooze + threshold helpers
# ---------------------------------------------------------------------------

def snooze_application(
    user_id: int, application_id: int, until: date_cls,
) -> None:
    with get_session() as session:
        app = session.get(Application, application_id)
        if app is None or app.user_id != user_id:
            raise PermissionError("Application not found.")
        app.snooze_reminders_until = until
        session.commit()


def unsnooze_application(user_id: int, application_id: int) -> None:
    with get_session() as session:
        app = session.get(Application, application_id)
        if app is None or app.user_id != user_id:
            raise PermissionError("Application not found.")
        app.snooze_reminders_until = None
        session.commit()


def set_inactive_threshold(user_id: int, days: int) -> None:
    """Persist the user's inactivity-reminder threshold on their Telegram link.

    ``days <= 0`` disables inactivity reminders without unlinking Telegram.
    Raises ``PermissionError`` for users without a link.
    """
    with get_session() as session:
        link = session.execute(
            select(TelegramLink).where(TelegramLink.user_id == user_id)
        ).scalar_one_or_none()
        if link is None:
            raise PermissionError("Telegram not linked.")
        link.inactive_reminder_days = max(0, int(days))
        session.commit()
