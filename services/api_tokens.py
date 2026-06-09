"""Long-lived API tokens for the REST API.

Tokens are 32 bytes from ``secrets.token_urlsafe`` plus a ``jos_`` prefix
(JobOffer Scanner). Only the bcrypt hash is persisted; the first 8 chars
get stored separately as a public ``prefix`` column so per-request auth
can do an indexed lookup instead of bcrypt-comparing every row.

Token format on the wire: ``jos_<43-ish chars>``. The full string is hashed
with bcrypt; the ``jos_<first 5 chars after prefix>`` is the indexed lookup
column (8 chars total, with prefix). On verification:

  1. Split the incoming token, take its 8-char prefix.
  2. Look up all non-revoked, non-expired rows for that prefix.
  3. bcrypt-compare the full token to each row's ``token_hash``.
  4. First match -> authenticated; bump ``last_used_at``; return owner.

Prefix collisions are statistically negligible (~1 in 1.4e11) so the
iteration cost is effectively constant.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional

import bcrypt
from sqlalchemy import desc, select

from db.models import ApiToken
from db.session import get_session
from services._ownership import require_owned
from services.audit import record as _audit

logger = logging.getLogger(__name__)


class ApiTokenError(ValueError):
    """User-facing failure (not found, cross-user, missing name)."""


_TOKEN_PREFIX_HEAD = "jos_"
_PREFIX_LENGTH = 8  # how many chars of the FULL token we index on


@dataclass(frozen=True)
class IssuedToken:
    """Result of token creation. ``raw_token`` is shown to the user ONCE."""

    record: "ApiTokenRecord"
    raw_token: str


@dataclass
class ApiTokenRecord:
    id: int
    user_id: int
    name: str
    prefix: str
    expires_at: Optional[datetime]
    revoked_at: Optional[datetime]
    last_used_at: Optional[datetime]
    created_at: datetime

    @property
    def is_active(self) -> bool:
        if self.revoked_at is not None:
            return False
        if self.expires_at is not None and self.expires_at <= datetime.utcnow():
            return False
        return True


def _to_record(t: ApiToken) -> ApiTokenRecord:
    return ApiTokenRecord(
        id=t.id,
        user_id=t.user_id,
        name=t.name,
        prefix=t.prefix,
        expires_at=t.expires_at,
        revoked_at=t.revoked_at,
        last_used_at=t.last_used_at,
        created_at=t.created_at,
    )


def _hash(token: str) -> str:
    return bcrypt.hashpw(token.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify(token: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(token.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# Owner-facing
# ---------------------------------------------------------------------------

def issue(
    user_id: int,
    name: str,
    ttl_days: Optional[int] = None,
) -> IssuedToken:
    """Generate a fresh token. ``ttl_days=None`` issues a non-expiring token."""
    name = (name or "").strip()
    if not name:
        raise ApiTokenError("Give the token a recognisable name.")

    raw = _TOKEN_PREFIX_HEAD + secrets.token_urlsafe(32)
    expires_at = (
        datetime.utcnow() + timedelta(days=ttl_days) if ttl_days and ttl_days > 0 else None
    )

    with get_session() as session:
        row = ApiToken(
            user_id=user_id,
            name=name,
            prefix=raw[:_PREFIX_LENGTH],
            token_hash=_hash(raw),
            expires_at=expires_at,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        rec = _to_record(row)
    _audit(
        "api_token.create",
        user_id=user_id,
        details={"token_id": rec.id, "name": rec.name, "ttl_days": ttl_days},
    )
    return IssuedToken(record=rec, raw_token=raw)


def list_for_user(user_id: int) -> List[ApiTokenRecord]:
    with get_session() as session:
        rows = session.execute(
            select(ApiToken)
            .where(ApiToken.user_id == user_id)
            .order_by(desc(ApiToken.created_at))
        ).scalars().all()
        return [_to_record(r) for r in rows]


def revoke(user_id: int, token_id: int) -> None:
    with get_session() as session:
        row = require_owned(session, ApiToken, token_id, user_id, ApiTokenError, "Token not found.")
        if row.revoked_at is None:
            row.revoked_at = datetime.utcnow()
            session.commit()
    _audit(
        "api_token.revoke",
        user_id=user_id,
        details={"token_id": token_id},
    )


# ---------------------------------------------------------------------------
# Request-time verification
# ---------------------------------------------------------------------------

def verify(raw_token: str) -> Optional[int]:
    """Return the user_id this token belongs to, or None if invalid."""
    raw_token = (raw_token or "").strip()
    if not raw_token.startswith(_TOKEN_PREFIX_HEAD):
        return None
    prefix = raw_token[:_PREFIX_LENGTH]
    now = datetime.utcnow()
    with get_session() as session:
        candidates = session.execute(
            select(ApiToken).where(ApiToken.prefix == prefix)
        ).scalars().all()
        for row in candidates:
            if row.revoked_at is not None:
                continue
            if row.expires_at is not None and row.expires_at <= now:
                continue
            if _verify(raw_token, row.token_hash):
                row.last_used_at = now
                session.commit()
                _audit(
                    "api_token.used",
                    user_id=row.user_id,
                    details={"token_id": row.id},
                )
                return row.user_id
    return None
