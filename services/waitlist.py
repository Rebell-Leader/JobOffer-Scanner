"""Waitlist email capture (marketing-site lead collection).

Public + unauthenticated, so it's deliberately defensive: validate the email
shape, normalise it, rate-limit per source key, and dedupe on the unique
constraint (re-submitting the same address is a friendly no-op, not an error
or a count-pad). Never raises into the caller for a duplicate.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from db.models import WaitlistEntry
from db.session import get_session
from services.rate_limit import RateLimiter, RateLimitExceeded

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Generous but abuse-bounded: 20 signups / hour per source bucket.
_LIMITER = RateLimiter("waitlist", max_attempts=20, window_seconds=3600)


class WaitlistError(ValueError):
    """User-facing failure (bad email)."""


@dataclass(frozen=True)
class WaitlistResult:
    ok: bool
    already: bool   # True if the email was already on the list


def join_waitlist(email: str, source: str = "", rate_key: str = "global") -> WaitlistResult:
    email = (email or "").strip().lower()
    if not _EMAIL_RE.match(email):
        raise WaitlistError("Please enter a valid email address.")

    decision = _LIMITER.check(rate_key)
    if not decision.allowed:
        raise RateLimitExceeded(decision.retry_after)

    try:
        with get_session() as session:
            session.add(WaitlistEntry(email=email, source=(source or None)))
            session.commit()
        return WaitlistResult(ok=True, already=False)
    except IntegrityError:
        # Unique-constraint hit — already signed up. Friendly no-op.
        return WaitlistResult(ok=True, already=True)
    except Exception as exc:  # noqa: BLE001 - never leak internals to a public endpoint
        logger.warning("Waitlist insert failed: %s", exc)
        raise WaitlistError("Could not join the waitlist right now. Try again later.") from exc


def waitlist_count() -> int:
    with get_session() as session:
        return int(session.execute(
            select(func.count()).select_from(WaitlistEntry)
        ).scalar_one() or 0)
