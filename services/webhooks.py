"""Outbound webhooks — HMAC-signed POSTs when a subscribed event fires.

Each delivery sends a JSON body with these headers:

  * ``X-JobOffer-Event``     — the event kind ("stage.added", …)
  * ``X-JobOffer-Delivery``  — the WebhookDelivery row id (idempotency key)
  * ``X-JobOffer-Signature`` — ``sha256=<hex>`` HMAC of the raw body using the
    webhook's secret. Receivers verify by recomputing the HMAC.

Delivery model: ``dispatch_event`` is synchronous + testable (records a
``WebhookDelivery`` row per attempt). ``dispatch_event_durable`` is the
production entry point — when a Celery broker is configured it enqueues a
per-(webhook,event) task that retries with exponential backoff for
at-least-once delivery; without a broker it degrades to a fire-and-forget
daemon thread (``dispatch_event_background``). Both are best-effort from the
caller's view — a failing webhook NEVER breaks the user action that triggered
it.

Events fire for NEW activity going forward. Historical backfill (bulk import)
deliberately does NOT dispatch — you don't want "you were rejected" webhooks
for things that happened months ago.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import requests
from sqlalchemy import desc, select

from db.models import WEBHOOK_EVENTS, Webhook, WebhookDelivery
from db.session import get_session
from services._ownership import require_owned
from services.audit import record as _audit
from utils.env import env_bool, env_float, env_int

logger = logging.getLogger(__name__)

_DELIVERY_TIMEOUT = env_float("WEBHOOK_TIMEOUT", 8.0)


def _max_attempts() -> int:
    """Total delivery attempts before giving up (initial + retries)."""
    return max(1, env_int("WEBHOOK_MAX_ATTEMPTS", 5))


def _retry_backoff_base() -> float:
    """Base seconds for exponential retry backoff (base * 2**retry_index)."""
    return env_float("WEBHOOK_RETRY_BACKOFF", 10.0)


def retry_delay_for(retry_index: int) -> float:
    """Exponential backoff for a given retry number (0-based), capped at 1h."""
    return min(3600.0, _retry_backoff_base() * (2 ** max(0, retry_index)))


class WebhookError(ValueError):
    """User-facing failure (bad URL, unknown event, cross-user, not found)."""


@dataclass
class WebhookRecord:
    id: int
    user_id: int
    url: str
    secret: str
    events: List[str]
    active: bool
    created_at: datetime


@dataclass
class DeliveryRecord:
    id: int
    webhook_id: int
    event: str
    success: bool
    status_code: Optional[int]
    error: Optional[str]
    attempts: int
    created_at: datetime


def _to_record(w: Webhook) -> WebhookRecord:
    return WebhookRecord(
        id=w.id, user_id=w.user_id, url=w.url, secret=w.secret,
        events=list(w.events or []), active=w.active, created_at=w.created_at,
    )


def _to_delivery(d: WebhookDelivery) -> DeliveryRecord:
    return DeliveryRecord(
        id=d.id, webhook_id=d.webhook_id, event=d.event, success=d.success,
        status_code=d.status_code, error=d.error, attempts=d.attempts,
        created_at=d.created_at,
    )


# ---------------------------------------------------------------------------
# Owner-facing CRUD
# ---------------------------------------------------------------------------

def register_webhook(user_id: int, url: str, events: List[str]) -> WebhookRecord:
    url = (url or "").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise WebhookError("Webhook URL must start with http:// or https://")
    bad = [e for e in events if e not in WEBHOOK_EVENTS]
    if bad:
        raise WebhookError(f"Unknown event(s): {', '.join(bad)}")
    if not events:
        raise WebhookError("Subscribe to at least one event.")

    with get_session() as session:
        wh = Webhook(
            user_id=user_id, url=url,
            secret=secrets.token_urlsafe(32),
            events=list(events), active=True,
        )
        session.add(wh)
        session.commit()
        session.refresh(wh)
        rec = _to_record(wh)
    _audit("webhook.create", user_id=user_id,
           details={"webhook_id": rec.id, "url": url, "events": events})
    return rec


def list_webhooks(user_id: int) -> List[WebhookRecord]:
    with get_session() as session:
        rows = session.execute(
            select(Webhook).where(Webhook.user_id == user_id)
            .order_by(desc(Webhook.created_at))
        ).scalars().all()
        return [_to_record(r) for r in rows]


def set_active(user_id: int, webhook_id: int, active: bool) -> None:
    with get_session() as session:
        wh = require_owned(session, Webhook, webhook_id, user_id, WebhookError, "Webhook not found.")
        wh.active = bool(active)
        session.commit()


def delete_webhook(user_id: int, webhook_id: int) -> None:
    with get_session() as session:
        wh = require_owned(session, Webhook, webhook_id, user_id, WebhookError, "Webhook not found.")
        session.delete(wh)
        session.commit()
    _audit("webhook.delete", user_id=user_id, details={"webhook_id": webhook_id})


def list_deliveries(user_id: int, limit: int = 20) -> List[DeliveryRecord]:
    with get_session() as session:
        rows = session.execute(
            select(WebhookDelivery)
            .where(WebhookDelivery.user_id == user_id)
            .order_by(desc(WebhookDelivery.created_at))
            .limit(limit)
        ).scalars().all()
        return [_to_delivery(r) for r in rows]


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------

def sign(secret: str, body: bytes) -> str:
    """Return ``sha256=<hex>`` HMAC of ``body`` keyed by ``secret``."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def dispatch_event(user_id: int, event: str, payload: dict) -> List[DeliveryRecord]:
    """Deliver ``event`` to every active webhook of ``user_id`` subscribed to it.

    Synchronous; records a WebhookDelivery row per attempt. Returns the
    delivery records. Best-effort — individual failures are logged + recorded,
    never raised.
    """
    deliveries: List[DeliveryRecord] = []
    with get_session() as session:
        hooks = session.execute(
            select(Webhook)
            .where(Webhook.user_id == user_id)
            .where(Webhook.active.is_(True))
        ).scalars().all()
        targets = [_to_record(h) for h in hooks]

    for hook in targets:
        if event not in hook.events:
            continue
        deliveries.append(_deliver(hook, event, payload))
    return deliveries


def dispatch_event_background(user_id: int, event: str, payload: dict) -> None:
    """Fire-and-forget dispatch on a daemon thread (non-blocking for the UI)."""
    def _run():
        try:
            dispatch_event(user_id, event, payload)
        except Exception as exc:  # noqa: BLE001 - never surface
            logger.warning("Background webhook dispatch failed: %s", exc)

    threading.Thread(target=_run, daemon=True).start()


def _celery_delivery_enabled() -> bool:
    """True when a Celery broker is available for durable webhook delivery."""
    if not env_bool("WEBHOOK_ASYNC", True):
        return False
    try:
        from worker.celery_app import get_celery_app

        return get_celery_app() is not None
    except Exception:  # noqa: BLE001 - any import/celery issue => sync fallback
        return False


def _has_active_subscriber(user_id: int, event: str) -> bool:
    """Whether the user has any active webhook subscribed to ``event``.

    A cheap pre-check so we don't spawn a delivery thread / enqueue a task (or,
    in tests, race the in-memory connection) when nobody is listening — the
    common case. Best-effort: a query failure returns False (skip dispatch).
    """
    try:
        with get_session() as session:
            hooks = session.execute(
                select(Webhook)
                .where(Webhook.user_id == user_id)
                .where(Webhook.active.is_(True))
            ).scalars().all()
            return any(event in (h.events or []) for h in hooks)
    except Exception as exc:  # noqa: BLE001 - never raise into the caller
        logger.warning("Webhook subscriber check failed: %s", exc)
        return False


def dispatch_event_durable(user_id: int, event: str, payload: dict) -> None:
    """Production dispatch entry point.

    With a Celery broker: create a pending delivery row per matching webhook
    and enqueue a task that retries with exponential backoff (at-least-once).
    Without a broker: degrade to the fire-and-forget daemon thread. Never
    raises into the caller.
    """
    if not _has_active_subscriber(user_id, event):
        return  # nobody listening — skip the thread/queue entirely
    if not _celery_delivery_enabled():
        dispatch_event_background(user_id, event, payload)
        return
    try:
        from worker.tasks import deliver_webhook_task

        with get_session() as session:
            hooks = session.execute(
                select(Webhook)
                .where(Webhook.user_id == user_id)
                .where(Webhook.active.is_(True))
            ).scalars().all()
            targets = [_to_record(h) for h in hooks if event in (h.events or [])]

        for hook in targets:
            delivery_id = _create_delivery(hook, event, payload)
            deliver_webhook_task.delay(delivery_id)
    except Exception as exc:  # noqa: BLE001 - fall back, never surface
        logger.warning("Durable webhook enqueue failed (%s); using thread.", exc)
        dispatch_event_background(user_id, event, payload)


def _create_delivery(hook: WebhookRecord, event: str, payload: dict) -> int:
    """Persist a pending (attempts=0) delivery row and return its id."""
    body_dict = {"event": event, "data": payload, "sent_at": datetime.utcnow().isoformat()}
    with get_session() as session:
        row = WebhookDelivery(
            webhook_id=hook.id, user_id=hook.user_id, event=event,
            payload=body_dict, attempts=0,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row.id


def attempt_delivery(delivery_id: int) -> bool:
    """POST one (existing) delivery row to its webhook; record + return success.

    Idempotent to re-run: each call is one attempt and increments ``attempts``.
    Re-builds the signed body from the stored payload so retries are byte-stable.
    Returns True on a 2xx, False otherwise (caller decides whether to retry).
    """
    with get_session() as session:
        row = session.get(WebhookDelivery, delivery_id)
        if row is None:
            return False
        hook = session.get(Webhook, row.webhook_id)
        if hook is None:
            return False
        body_dict = dict(row.payload or {})
        url, secret = hook.url, hook.secret
        event = row.event

    body = json.dumps(body_dict, default=str, ensure_ascii=False).encode("utf-8")

    status_code: Optional[int] = None
    error: Optional[str] = None
    success = False
    try:
        resp = requests.post(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-JobOffer-Event": event,
                "X-JobOffer-Delivery": str(delivery_id),
                "X-JobOffer-Signature": sign(secret, body),
            },
            timeout=_DELIVERY_TIMEOUT,
        )
        status_code = resp.status_code
        success = 200 <= resp.status_code < 300
        if not success:
            error = f"HTTP {resp.status_code}: {resp.text[:200]}"
    except Exception as exc:  # noqa: BLE001 - recorded, not raised
        error = str(exc)[:500]

    with get_session() as session:
        row = session.get(WebhookDelivery, delivery_id)
        row.attempts += 1
        row.success = success
        row.status_code = status_code
        row.error = error
        session.commit()
    return success


def _deliver(hook: WebhookRecord, event: str, payload: dict) -> DeliveryRecord:
    """Create a delivery row and POST it once (synchronous, single attempt)."""
    delivery_id = _create_delivery(hook, event, payload)
    attempt_delivery(delivery_id)
    with get_session() as session:
        return _to_delivery(session.get(WebhookDelivery, delivery_id))


def redeliver(user_id: int, delivery_id: int) -> DeliveryRecord:
    """Retry a previous delivery (re-POSTs the SAME payload to its webhook)."""
    with get_session() as session:
        row = require_owned(session, WebhookDelivery, delivery_id, user_id, WebhookError, "Delivery not found.")
        hook = session.get(Webhook, row.webhook_id)
        if hook is None:
            raise WebhookError("Webhook no longer exists.")
        hook_rec = _to_record(hook)
        original_data = (row.payload or {}).get("data", {})
        event = row.event

    # Re-dispatch produces a NEW delivery row (keeps the audit trail of
    # both the original failure and the retry).
    return _deliver(hook_rec, event, original_data)
