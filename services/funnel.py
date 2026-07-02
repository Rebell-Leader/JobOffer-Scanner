"""Operator-facing funnel & cohort metrics — the founder's validation dashboard.

Distinct from ``services/analytics`` (which is per-USER product analytics).
This computes CROSS-user business metrics straight from tables we already own,
so demand validation needs no third-party product-analytics tool:

  * signups, activation (signup -> first analysis), the aha metric (>=3
    analyses in a user's first week), engagement, free->paid conversion, and
    estimated COGS/revenue — all from ``users``, ``usage_events``,
    ``subscriptions``, ``llm_usage``, ``audit_events``.

Surfaced by ``worker/funnel_report.py`` (cron-friendly CLI). Read-only.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, select

from db.models import LlmUsage, Subscription, UsageEvent, User
from db.session import get_session


@dataclass
class FunnelReport:
    window_days: int
    generated_at: str

    total_users: int
    new_users: int                 # signed up within the window
    activated_users: int           # ran >=1 analysis ever
    activation_rate: float         # activated / total
    aha_users: int                 # >=3 analyses within 7 days of THEIR signup
    aha_rate: float                # aha / total

    analyses_in_window: int
    active_users_in_window: int    # ran >=1 analysis in the window
    avg_analyses_per_active: float

    paying_users: int              # active (non-free) subscription
    free_to_paid_rate: float       # paying / total

    est_cogs_usd_window: float     # LLM spend in the window
    est_mrr_usd: float             # from current paid subscriptions

    def to_dict(self) -> dict:
        return asdict(self)


# Monthly price per tier (USD) for the MRR estimate. Mirrors the marketing
# pricing; kept here (not imported from billing) so the report is a pure
# read-model with no billing dependency.
_TIER_MRR = {"pro": 12.0, "power": 24.0}


def _count(session, stmt) -> int:
    return int(session.execute(stmt).scalar_one() or 0)


def compute_funnel(window_days: int = 30, now: Optional[datetime] = None) -> FunnelReport:
    now = now or datetime.utcnow()
    cutoff = now - timedelta(days=window_days)

    with get_session() as session:
        total_users = _count(session, select(func.count()).select_from(User))
        new_users = _count(
            session,
            select(func.count()).select_from(User).where(User.created_at >= cutoff),
        )

        # Activation: distinct users with >=1 analysis event, ever.
        activated_users = _count(
            session,
            select(func.count(func.distinct(UsageEvent.user_id)))
            .where(UsageEvent.kind == "analysis"),
        )

        # Aha: >=3 analyses within 7 days of that user's signup. Computed in
        # Python over a bounded join — fine at validation scale (hundreds).
        aha_users = 0
        users = session.execute(select(User.id, User.created_at)).all()
        for uid, created in users:
            n = _count(
                session,
                select(func.count()).select_from(UsageEvent).where(
                    UsageEvent.user_id == uid,
                    UsageEvent.kind == "analysis",
                    UsageEvent.created_at <= created + timedelta(days=7),
                ),
            )
            if n >= 3:
                aha_users += 1

        analyses_in_window = _count(
            session,
            select(func.count()).select_from(UsageEvent).where(
                UsageEvent.kind == "analysis", UsageEvent.created_at >= cutoff,
            ),
        )
        active_users_in_window = _count(
            session,
            select(func.count(func.distinct(UsageEvent.user_id))).where(
                UsageEvent.kind == "analysis", UsageEvent.created_at >= cutoff,
            ),
        )

        # Paying = active subscription on a paid tier.
        paid_rows = session.execute(
            select(Subscription.tier).where(
                Subscription.status.in_(("active", "past_due")),
                Subscription.tier.in_(tuple(_TIER_MRR)),
            )
        ).scalars().all()
        paying_users = len(paid_rows)
        est_mrr = sum(_TIER_MRR.get(t, 0.0) for t in paid_rows)

        cogs_micro = _count(
            session,
            select(func.coalesce(func.sum(LlmUsage.cost_micro_usd), 0)).where(
                LlmUsage.created_at >= cutoff
            ),
        )

    def _rate(n: int, d: int) -> float:
        return round(n / d, 4) if d else 0.0

    return FunnelReport(
        window_days=window_days,
        generated_at=now.isoformat(timespec="seconds"),
        total_users=total_users,
        new_users=new_users,
        activated_users=activated_users,
        activation_rate=_rate(activated_users, total_users),
        aha_users=aha_users,
        aha_rate=_rate(aha_users, total_users),
        analyses_in_window=analyses_in_window,
        active_users_in_window=active_users_in_window,
        avg_analyses_per_active=round(
            analyses_in_window / active_users_in_window, 2
        ) if active_users_in_window else 0.0,
        paying_users=paying_users,
        free_to_paid_rate=_rate(paying_users, total_users),
        est_cogs_usd_window=round(cogs_micro / 1_000_000, 2),
        est_mrr_usd=round(est_mrr, 2),
    )


def render_text(report: FunnelReport) -> str:
    r = report
    pct = lambda x: f"{x * 100:.1f}%"  # noqa: E731
    return "\n".join([
        f"Funnel report · {r.generated_at} · last {r.window_days} days",
        "─" * 52,
        f"Users (total / new in window): {r.total_users} / {r.new_users}",
        f"Activated (ran >=1 analysis): {r.activated_users}  ({pct(r.activation_rate)})",
        f"Aha (>=3 analyses in first week): {r.aha_users}  ({pct(r.aha_rate)})",
        "",
        f"Analyses in window: {r.analyses_in_window}",
        f"Active users in window: {r.active_users_in_window}"
        f"  (avg {r.avg_analyses_per_active}/user)",
        "",
        f"Paying users: {r.paying_users}  (free→paid {pct(r.free_to_paid_rate)})",
        f"Est. MRR: ${r.est_mrr_usd:.2f}   ·   Est. COGS (window): ${r.est_cogs_usd_window:.2f}",
        "─" * 52,
        "60-day gate: >=10 paying -> invest · 3-9 -> iterate · <3 -> reconsider.",
    ])
