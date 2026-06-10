"""Subscription tiers, quota enforcement, and Stripe integration.

Tier model
----------
Limits live HERE in code (env-overridable via ``TIER_LIMITS_JSON``), not in the
DB — changing a quota is a deploy, not a migration. A user's tier comes from
their ``subscriptions`` row (mirrored from Stripe by webhook). No row means:

  * ``free``      when billing is enabled (``STRIPE_SECRET_KEY`` set), or
  * ``unlimited`` when it isn't — billing must NEVER constrain self-hosters;
    with no Stripe key everything behaves exactly as before this module existed.

Enforcement
-----------
``check_and_record_analysis`` (wired into ``analysis_runner.check_user_quota``)
gates analyses per rolling window + the tier's LLM spend budget, then meters
one ``usage_events`` row. ``check_artifact_quota`` (wired into
``services/tailoring``) counts existing ``application_artifacts`` rows — no
extra bookkeeping. ``clamp_model`` downgrades "detailed" to "fast" for tiers
without detailed-model access. ``api_access_allowed`` gates the REST API.
All raise :class:`TierLimitExceeded` with an upgrade-oriented message.

Stripe
------
The ``stripe`` package is an optional extra (``[billing]``); all imports are
lazy. ``create_checkout_session`` / ``create_portal_session`` return URLs for
the UI to surface. ``handle_webhook_event`` (mounted at
``POST /v1/billing/webhook``) verifies the signature and mirrors subscription
state into the ``subscriptions`` table. Price→tier mapping comes from
``STRIPE_PRICE_PRO`` / ``STRIPE_PRICE_POWER``.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Optional

from sqlalchemy import func, select

from db.models import ApplicationArtifact, Subscription, UsageEvent
from db.session import get_session
from services.audit import record as _audit
from utils.env import env_int, env_str

logger = logging.getLogger(__name__)


class BillingError(ValueError):
    """Operator/config-facing billing failure (Stripe missing, bad tier, …)."""


class TierLimitExceeded(Exception):
    """A tier quota was hit. Message is user-facing and suggests upgrading."""

    def __init__(self, message: str, tier: str, limit_kind: str):
        self.tier = tier
        self.limit_kind = limit_kind
        super().__init__(message)


# ---------------------------------------------------------------------------
# Tier definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TierLimits:
    name: str
    label: str
    analyses_per_window: int      # -1 = unlimited
    artifacts_per_window: int     # tailored CVs + cover letters; -1 = unlimited
    budget_usd: float             # LLM spend cap over the window; 0 = unlimited
    detailed_model: bool          # may use the "detailed" model tier
    api_access: bool              # may call the REST API / extension


# Rolling window for all tier counters (days). Rolling avoids end-of-month
# gaming and matches services/usage.spend_usd semantics.
def _window_days() -> int:
    return env_int("TIER_WINDOW_DAYS", 30)


_DEFAULT_TIERS: Dict[str, TierLimits] = {
    "free": TierLimits(
        name="free", label="Free",
        analyses_per_window=5, artifacts_per_window=2,
        budget_usd=0.25, detailed_model=False, api_access=False,
    ),
    "pro": TierLimits(
        name="pro", label="Pro",
        analyses_per_window=50, artifacts_per_window=30,
        budget_usd=5.0, detailed_model=True, api_access=False,
    ),
    "power": TierLimits(
        name="power", label="Power",
        analyses_per_window=200, artifacts_per_window=-1,
        budget_usd=15.0, detailed_model=True, api_access=True,
    ),
    # Self-hosted / billing-disabled: no tier constraints at all. The global
    # operator knobs (RL_ANALYSIS_*, LLM_BUDGET_USD) still apply independently.
    "unlimited": TierLimits(
        name="unlimited", label="Self-hosted",
        analyses_per_window=-1, artifacts_per_window=-1,
        budget_usd=0.0, detailed_model=True, api_access=True,
    ),
}


def tier_table() -> Dict[str, TierLimits]:
    """The active tier table; ``TIER_LIMITS_JSON`` overrides fields per tier."""
    raw = env_str("TIER_LIMITS_JSON")
    if not raw:
        return _DEFAULT_TIERS
    try:
        overrides = json.loads(raw)
        table = dict(_DEFAULT_TIERS)
        for name, fields in overrides.items():
            base = table.get(name) or _DEFAULT_TIERS["free"]
            table[name] = TierLimits(**{**base.__dict__, **fields, "name": name})
        return table
    except Exception as exc:  # noqa: BLE001 - bad override must not break quota checks
        logger.warning("Ignoring invalid TIER_LIMITS_JSON (%s); using defaults.", exc)
        return _DEFAULT_TIERS


def billing_enabled() -> bool:
    """Tier enforcement is on only when Stripe is configured."""
    return bool(os.getenv("STRIPE_SECRET_KEY"))


def get_tier(user_id: int) -> str:
    """The user's current tier name."""
    if not billing_enabled():
        return "unlimited"
    with get_session() as session:
        sub = session.execute(
            select(Subscription).where(Subscription.user_id == user_id)
        ).scalar_one_or_none()
        if sub is None or sub.status not in ("active", "past_due"):
            return "free"
        return sub.tier if sub.tier in tier_table() else "free"


def get_limits(user_id: int) -> TierLimits:
    return tier_table()[get_tier(user_id)]


# ---------------------------------------------------------------------------
# Usage counting + enforcement
# ---------------------------------------------------------------------------

def _window_cutoff() -> datetime:
    return datetime.utcnow() - timedelta(days=_window_days())


def analyses_used(user_id: int) -> int:
    """Analyses run in the current rolling window."""
    with get_session() as session:
        return session.execute(
            select(func.count()).select_from(UsageEvent).where(
                UsageEvent.user_id == user_id,
                UsageEvent.kind == "analysis",
                UsageEvent.created_at >= _window_cutoff(),
            )
        ).scalar_one()


def artifacts_used(user_id: int) -> int:
    """Tailored artifacts generated in the current rolling window."""
    with get_session() as session:
        return session.execute(
            select(func.count()).select_from(ApplicationArtifact).where(
                ApplicationArtifact.user_id == user_id,
                ApplicationArtifact.created_at >= _window_cutoff(),
            )
        ).scalar_one()


def check_and_record_analysis(user_id: int) -> None:
    """Gate one analysis against the tier quota + budget, then meter it.

    Called from ``analysis_runner.check_user_quota`` (so every entry point —
    UI, API, background — passes through). Raises :class:`TierLimitExceeded`.
    """
    limits = get_limits(user_id)

    if limits.analyses_per_window >= 0:
        used = analyses_used(user_id)
        if used >= limits.analyses_per_window:
            raise TierLimitExceeded(
                f"You've used all {limits.analyses_per_window} analyses on the "
                f"{limits.label} plan for this {_window_days()}-day period. "
                "Upgrade your plan to keep analyzing.",
                tier=limits.name, limit_kind="analyses",
            )

    if limits.budget_usd > 0:
        from services.usage import spend_usd
        spent = spend_usd(user_id, window_seconds=_window_days() * 86400)
        if spent >= limits.budget_usd:
            raise TierLimitExceeded(
                f"The {limits.label} plan's usage allowance is exhausted for "
                f"this {_window_days()}-day period. Upgrade for more capacity.",
                tier=limits.name, limit_kind="budget",
            )

    # Metering is a side-effect: per the codebase convention it must never
    # break the analysis it meters. A failed insert fails OPEN (the user gets
    # one un-metered run) while the quota CHECK above stays fail-closed.
    try:
        with get_session() as session:
            session.add(UsageEvent(user_id=user_id, kind="analysis"))
            session.commit()
    except Exception as exc:  # noqa: BLE001 - log-and-continue
        logger.warning("Failed to meter analysis for user %s: %s", user_id, exc)


def check_artifact_quota(user_id: int) -> None:
    """Gate one tailored CV / cover-letter generation against the tier quota."""
    limits = get_limits(user_id)
    if limits.artifacts_per_window < 0:
        return
    used = artifacts_used(user_id)
    if used >= limits.artifacts_per_window:
        raise TierLimitExceeded(
            f"You've generated all {limits.artifacts_per_window} tailored "
            f"documents on the {limits.label} plan for this "
            f"{_window_days()}-day period. Upgrade to generate more.",
            tier=limits.name, limit_kind="artifacts",
        )


def clamp_model(user_id: Optional[int], requested: str) -> str:
    """Downgrade "detailed" to "fast" for tiers without detailed-model access."""
    if user_id is None or requested != "detailed":
        return requested
    return requested if get_limits(user_id).detailed_model else "fast"


def api_access_allowed(user_id: int) -> bool:
    return get_limits(user_id).api_access


def plan_summary(user_id: int) -> dict:
    """Current plan + usage for the UI / GET /v1/billing/plan."""
    limits = get_limits(user_id)
    return {
        "tier": limits.name,
        "label": limits.label,
        "window_days": _window_days(),
        "analyses_used": analyses_used(user_id),
        "analyses_limit": limits.analyses_per_window,
        "artifacts_used": artifacts_used(user_id),
        "artifacts_limit": limits.artifacts_per_window,
        "detailed_model": limits.detailed_model,
        "api_access": limits.api_access,
        "billing_enabled": billing_enabled(),
    }


# ---------------------------------------------------------------------------
# Stripe (lazy, optional)
# ---------------------------------------------------------------------------

def _get_stripe():
    """Return the configured stripe module, or raise BillingError."""
    key = os.getenv("STRIPE_SECRET_KEY")
    if not key:
        raise BillingError("Billing is not configured (STRIPE_SECRET_KEY unset).")
    try:
        import stripe  # lazy — [billing] extra
    except ImportError as exc:
        raise BillingError(
            "The stripe package is not installed (pip install '.[billing]')."
        ) from exc
    stripe.api_key = key
    return stripe


def _price_for_tier(tier: str) -> str:
    env_name = {"pro": "STRIPE_PRICE_PRO", "power": "STRIPE_PRICE_POWER"}.get(tier)
    price = os.getenv(env_name) if env_name else None
    if not price:
        raise BillingError(f"No Stripe price configured for tier {tier!r}.")
    return price


def _tier_for_price(price_id: str) -> Optional[str]:
    for tier, env_name in (("pro", "STRIPE_PRICE_PRO"), ("power", "STRIPE_PRICE_POWER")):
        if price_id and os.getenv(env_name) == price_id:
            return tier
    return None


def create_checkout_session(user_id: int, tier: str, user_email: str = "") -> str:
    """Create a Stripe Checkout session for an upgrade; returns the URL."""
    if tier not in ("pro", "power"):
        raise BillingError(f"Unknown paid tier {tier!r}.")
    stripe = _get_stripe()
    base = os.getenv("APP_BASE_URL", "http://localhost:5000").rstrip("/")
    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": _price_for_tier(tier), "quantity": 1}],
        success_url=f"{base}/?billing=success",
        cancel_url=f"{base}/?billing=canceled",
        customer_email=user_email or None,
        client_reference_id=str(user_id),
        metadata={"user_id": str(user_id), "tier": tier},
    )
    _audit("billing.checkout.started", user_id=user_id, details={"tier": tier})
    return session.url


def create_portal_session(user_id: int) -> str:
    """Stripe customer-portal URL for managing/canceling a subscription."""
    stripe = _get_stripe()
    with get_session() as session:
        sub = session.execute(
            select(Subscription).where(Subscription.user_id == user_id)
        ).scalar_one_or_none()
        customer_id = sub.stripe_customer_id if sub else None
    if not customer_id:
        raise BillingError("No active subscription to manage.")
    base = os.getenv("APP_BASE_URL", "http://localhost:5000").rstrip("/")
    portal = stripe.billing_portal.Session.create(
        customer=customer_id, return_url=f"{base}/",
    )
    return portal.url


# ---------------------------------------------------------------------------
# Webhook mirroring (Stripe -> subscriptions table)
# ---------------------------------------------------------------------------

def verify_webhook(payload: bytes, signature: str):
    """Verify a Stripe webhook signature; returns the parsed event."""
    stripe = _get_stripe()
    secret = os.getenv("STRIPE_WEBHOOK_SECRET")
    if not secret:
        raise BillingError("STRIPE_WEBHOOK_SECRET is not configured.")
    try:
        return stripe.Webhook.construct_event(payload, signature, secret)
    except Exception as exc:  # noqa: BLE001 - bad signature/body => reject
        raise BillingError(f"Webhook verification failed: {exc}") from exc


def _upsert_subscription(
    user_id: int, tier: str, status: str,
    customer_id: Optional[str], subscription_id: Optional[str],
    period_end: Optional[datetime],
) -> None:
    with get_session() as session:
        sub = session.execute(
            select(Subscription).where(Subscription.user_id == user_id)
        ).scalar_one_or_none()
        created = sub is None
        if sub is None:
            sub = Subscription(user_id=user_id)
            session.add(sub)
        sub.tier = tier
        sub.status = status
        if customer_id:
            sub.stripe_customer_id = customer_id
        if subscription_id:
            sub.stripe_subscription_id = subscription_id
        sub.current_period_end = period_end
        session.commit()
    _audit(
        "billing.subscription.created" if created else "billing.subscription.updated",
        user_id=user_id, details={"tier": tier, "status": status},
    )


def handle_webhook_event(event) -> bool:
    """Mirror one verified Stripe event into the subscriptions table.

    Returns True if the event changed local state (False = ignored kind).
    Idempotent: events re-deliver, and upserting the same state is a no-op.
    """
    kind = event.get("type", "")
    obj = (event.get("data") or {}).get("object") or {}

    if kind == "checkout.session.completed":
        meta = obj.get("metadata") or {}
        user_id = int(meta.get("user_id") or obj.get("client_reference_id") or 0)
        tier = meta.get("tier") or "pro"
        if not user_id:
            logger.warning("checkout.session.completed without a user_id; ignoring.")
            return False
        _upsert_subscription(
            user_id, tier=tier, status="active",
            customer_id=obj.get("customer"),
            subscription_id=obj.get("subscription"),
            period_end=None,
        )
        return True

    if kind in ("customer.subscription.updated", "customer.subscription.deleted"):
        sub_id = obj.get("id")
        with get_session() as session:
            sub = session.execute(
                select(Subscription).where(
                    Subscription.stripe_subscription_id == sub_id
                )
            ).scalar_one_or_none()
            if sub is None:
                logger.warning("Stripe subscription %s has no local row; ignoring.", sub_id)
                return False
            user_id = sub.user_id
        if kind == "customer.subscription.deleted":
            _upsert_subscription(
                user_id, tier="free", status="canceled",
                customer_id=None, subscription_id=None, period_end=None,
            )
            _audit("billing.subscription.canceled", user_id=user_id)
            return True
        # updated: refresh status, period end, and tier (plan switches).
        status = obj.get("status") or "active"
        period_end = None
        ts = obj.get("current_period_end")
        if ts:
            period_end = datetime.utcfromtimestamp(int(ts))
        items = ((obj.get("items") or {}).get("data")) or []
        price_id = (items[0].get("price") or {}).get("id") if items else None
        tier = _tier_for_price(price_id or "")
        with get_session() as session:
            row = session.execute(
                select(Subscription).where(Subscription.user_id == user_id)
            ).scalar_one()
            row.status = status
            row.current_period_end = period_end
            if tier:
                row.tier = tier
            session.commit()
        _audit("billing.subscription.updated", user_id=user_id,
               details={"status": status, "tier": tier})
        return True

    return False
