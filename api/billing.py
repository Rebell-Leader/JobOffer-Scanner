"""Billing endpoints: the Stripe webhook + plan introspection.

The webhook is UNAUTHENTICATED by design — Stripe can't send a bearer token —
and is instead verified by the ``stripe-signature`` header against
``STRIPE_WEBHOOK_SECRET``. It returns 503 when billing isn't configured so a
misrouted deployment fails loudly rather than silently dropping events.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from api.auth import require_user
from services.billing import (
    BillingError,
    billing_enabled,
    handle_webhook_event,
    plan_summary,
    verify_webhook,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/billing")


@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(default="", alias="stripe-signature"),
) -> dict:
    if not billing_enabled():
        raise HTTPException(status_code=503, detail="Billing is not configured.")
    payload = await request.body()
    try:
        event = verify_webhook(payload, stripe_signature)
    except BillingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    handled = handle_webhook_event(event)
    return {"received": True, "handled": handled}


@router.get("/plan")
def get_plan(user_id: int = Depends(require_user)) -> dict:
    """Current tier + usage for the authenticated user."""
    return plan_summary(user_id)
