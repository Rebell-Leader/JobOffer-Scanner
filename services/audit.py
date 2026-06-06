"""Audit-event recording + retrieval.

Two interfaces:

  * ``record(kind, user_id=None, details=None)`` — single write. Picks up the
    current request_id from the logging contextvar so the audit row joins
    cleanly to the log line that recorded the event. Failures here NEVER
    bubble into the caller's flow — auditing must never break the operation
    being audited.
  * ``list_for_user(user_id)`` / ``list_recent(limit)`` — read paths. The
    per-user view is shown in the sidebar; ``list_recent`` is the system-wide
    one for the metrics CLI.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from sqlalchemy import desc, select

from db.models import AUDIT_KINDS, AuditEvent
from db.session import get_session
from utils.logging_setup import get_request_id

logger = logging.getLogger(__name__)


@dataclass
class AuditRecord:
    id: int
    user_id: Optional[int]
    kind: str
    details: dict
    ip: Optional[str]
    request_id: Optional[str]
    created_at: datetime


def _to_record(ev: AuditEvent) -> AuditRecord:
    return AuditRecord(
        id=ev.id,
        user_id=ev.user_id,
        kind=ev.kind,
        details=ev.details or {},
        ip=ev.ip,
        request_id=ev.request_id,
        created_at=ev.created_at,
    )


def record(
    kind: str,
    *,
    user_id: Optional[int] = None,
    details: Optional[dict] = None,
    ip: Optional[str] = None,
) -> None:
    """Append one audit row. Best-effort: errors are logged, not raised."""
    if kind not in AUDIT_KINDS:
        # Don't reject — record as-is with a logged warning so a typo doesn't
        # silently drop events.
        logger.warning("audit: unknown kind %r — recording anyway.", kind)

    try:
        with get_session() as session:
            session.add(
                AuditEvent(
                    user_id=user_id,
                    kind=kind,
                    details=details or None,
                    ip=ip,
                    request_id=get_request_id(),
                )
            )
            session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("audit: failed to record %s: %s", kind, exc)


def list_for_user(user_id: int, limit: int = 50) -> List[AuditRecord]:
    with get_session() as session:
        rows = session.execute(
            select(AuditEvent)
            .where(AuditEvent.user_id == user_id)
            .order_by(desc(AuditEvent.created_at))
            .limit(limit)
        ).scalars().all()
        return [_to_record(r) for r in rows]


def list_recent(limit: int = 100) -> List[AuditRecord]:
    with get_session() as session:
        rows = session.execute(
            select(AuditEvent)
            .order_by(desc(AuditEvent.created_at))
            .limit(limit)
        ).scalars().all()
        return [_to_record(r) for r in rows]


def count_by_kind(kind: str, since: Optional[datetime] = None) -> int:
    with get_session() as session:
        q = select(AuditEvent).where(AuditEvent.kind == kind)
        if since is not None:
            q = q.where(AuditEvent.created_at >= since)
        return len(session.execute(q).scalars().all())
