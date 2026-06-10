"""Public waitlist endpoint for the marketing site.

Unauthenticated by design — the landing page (a different origin) POSTs an
email here. CORS is opened narrowly to the configured site origin
(``LANDING_ORIGIN``, default ``*`` for dev). Errors are mapped to clean codes;
duplicates are a 200 (idempotent), bad emails 400, rate-limit 429.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.rate_limit import RateLimitExceeded
from services.waitlist import WaitlistError, join_waitlist

router = APIRouter()


class WaitlistRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    source: str = Field(default="", max_length=64)


def landing_origin() -> str:
    return os.getenv("LANDING_ORIGIN", "*")


@router.post("/waitlist")
def post_waitlist(body: WaitlistRequest) -> dict:
    try:
        result = join_waitlist(body.email, source=body.source)
    except WaitlistError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    return {"ok": result.ok, "already": result.already}
