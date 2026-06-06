"""Public read-only share links for saved applications.

Anyone holding the token can view a read-only copy of the analysis — useful
for "look at this offer with me" feedback loops without granting login
access. Tokens are opaque, URL-safe random strings; default TTL is 7 days
but the owner can pick any value (or no expiry). Revocation is immediate;
the table records view counts so the owner can spot unexpected access.

Ownership semantics:
  * ``create_share`` / ``list_shares_for_application`` / ``revoke`` are
    ownership-scoped to the application owner.
  * ``get_view`` is the public read path — it only checks token validity
    (existence + not revoked + not expired). It increments the view count
    and records an audit row stamped with ``viewer_ip`` when provided.

Token strength: 32 bytes from ``secrets.token_urlsafe`` → ~43 ascii chars
of entropy. Stored as the raw token (not a hash) so we can look it up by
the URL; safe because the only thing a leaked token grants is exactly
the read access the owner intentionally created.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy import desc, select

from db.models import Application, ApplicationShare
from db.session import get_session
from services.audit import record as _audit


class ShareError(ValueError):
    """User-facing failure (not found, expired, revoked, cross-user)."""


_TOKEN_BYTES = 32


@dataclass
class ShareRecord:
    id: int
    application_id: int
    user_id: int
    token: str
    expires_at: Optional[datetime]
    revoked_at: Optional[datetime]
    include_artifacts: bool
    view_count: int
    last_viewed_at: Optional[datetime]
    created_at: datetime

    @property
    def is_active(self) -> bool:
        if self.revoked_at is not None:
            return False
        if self.expires_at is not None and self.expires_at <= datetime.utcnow():
            return False
        return True


def _to_record(s: ApplicationShare) -> ShareRecord:
    return ShareRecord(
        id=s.id,
        application_id=s.application_id,
        user_id=s.user_id,
        token=s.token,
        expires_at=s.expires_at,
        revoked_at=s.revoked_at,
        include_artifacts=s.include_artifacts,
        view_count=s.view_count,
        last_viewed_at=s.last_viewed_at,
        created_at=s.created_at,
    )


# ---------------------------------------------------------------------------
# Owner-facing
# ---------------------------------------------------------------------------

def create_share(
    user_id: int,
    application_id: int,
    ttl_days: Optional[int] = 7,
    include_artifacts: bool = False,
) -> ShareRecord:
    """Create a fresh share token for one application.

    ``ttl_days=None`` skips expiry; ``ttl_days=0`` is treated as "no TTL"
    too (zero-day expiry would be pointless and is almost certainly a UI
    bug). Each call creates a NEW token — the owner can revoke + recreate
    to rotate.
    """
    with get_session() as session:
        app = session.get(Application, application_id)
        if app is None or app.user_id != user_id:
            raise ShareError("Application not found.")
        expires_at = None
        if ttl_days and ttl_days > 0:
            expires_at = datetime.utcnow() + timedelta(days=ttl_days)
        share = ApplicationShare(
            application_id=application_id,
            user_id=user_id,
            token=secrets.token_urlsafe(_TOKEN_BYTES),
            expires_at=expires_at,
            include_artifacts=bool(include_artifacts),
        )
        session.add(share)
        session.commit()
        session.refresh(share)
        rec = _to_record(share)
    _audit(
        "share.create",
        user_id=user_id,
        details={
            "application_id": application_id,
            "share_id": rec.id,
            "ttl_days": ttl_days,
            "include_artifacts": include_artifacts,
        },
    )
    return rec


def list_shares_for_application(
    user_id: int, application_id: int,
) -> List[ShareRecord]:
    with get_session() as session:
        app = session.get(Application, application_id)
        if app is None or app.user_id != user_id:
            raise ShareError("Application not found.")
        rows = session.execute(
            select(ApplicationShare)
            .where(ApplicationShare.application_id == application_id)
            .order_by(desc(ApplicationShare.created_at))
        ).scalars().all()
        return [_to_record(r) for r in rows]


def revoke(user_id: int, share_id: int) -> None:
    with get_session() as session:
        share = session.get(ApplicationShare, share_id)
        if share is None or share.user_id != user_id:
            raise ShareError("Share not found.")
        if share.revoked_at is None:
            share.revoked_at = datetime.utcnow()
            session.commit()
    _audit("share.revoke", user_id=user_id, details={"share_id": share_id})


# ---------------------------------------------------------------------------
# Public read path
# ---------------------------------------------------------------------------

@dataclass
class PublicView:
    """What a holder of a valid token sees. No user_id leak."""

    company_name: str
    job_title: str
    location: Optional[str]
    status: str
    verdict: Optional[str]
    verdict_light: Optional[str]
    ats_score: Optional[int]
    analysis_json: dict
    created_at: datetime
    artifacts: List[dict]


def get_view(token: str, viewer_ip: Optional[str] = None) -> PublicView:
    """Resolve a token to its public view.

    Raises ``ShareError`` for any non-active token (unknown / revoked /
    expired). On a successful resolve, increments the view counter and
    records an audit row (with the share owner's user_id) so the owner
    can see who accessed when.
    """
    token = (token or "").strip()
    if not token:
        raise ShareError("Missing share token.")

    with get_session() as session:
        share = session.execute(
            select(ApplicationShare).where(ApplicationShare.token == token)
        ).scalar_one_or_none()
        if share is None:
            raise ShareError("This share link is not recognised.")
        if share.revoked_at is not None:
            raise ShareError("This share link has been revoked by the owner.")
        if share.expires_at is not None and share.expires_at <= datetime.utcnow():
            raise ShareError("This share link has expired.")

        app = session.get(Application, share.application_id)
        if app is None:
            raise ShareError("The underlying application no longer exists.")

        # Optional artifact bundle.
        from db.models import ApplicationArtifact  # local import

        artifact_rows: List[dict] = []
        if share.include_artifacts:
            arts = session.execute(
                select(ApplicationArtifact)
                .where(ApplicationArtifact.application_id == app.id)
                .order_by(desc(ApplicationArtifact.created_at))
            ).scalars().all()
            artifact_rows = [
                {
                    "id": a.id,
                    "kind": a.kind,
                    "content": a.content,
                    "created_at": a.created_at.isoformat(),
                }
                for a in arts
            ]

        share.view_count += 1
        share.last_viewed_at = datetime.utcnow()
        owner_id = share.user_id
        session.commit()

        view = PublicView(
            company_name=app.company_name,
            job_title=app.job_title,
            location=app.location,
            status=app.status,
            verdict=app.verdict,
            verdict_light=app.verdict_light,
            ats_score=app.ats_score,
            analysis_json=app.analysis_json or {},
            created_at=app.created_at,
            artifacts=artifact_rows,
        )

    _audit(
        "share.view",
        user_id=owner_id,
        details={"share_id": share.id if share else None, "ip": viewer_ip},
    )
    return view
