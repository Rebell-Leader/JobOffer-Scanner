"""Per-user job-search analytics derived from saved applications + stages.

Pure-ish service: pulls the data the user owns, computes counts/rates, returns
serializable dataclasses. Visualization lives in the UI layer.

What's here:
  * ``compute_overview`` — high-level counts (total apps, active, in interview,
    offers received, rejection rate).
  * ``compute_funnel`` — for each pipeline stage, the number of applications
    that reached it, plus stage-over-stage conversion rates.
  * ``compute_time_in_stage`` — average days between successive stages.
  * ``compute_verdict_outcome`` — does a Green/Yellow/Red verdict from our
    analyzer correlate with reaching offer / being rejected?
  * ``compute_rejection_stage`` — where in the funnel do rejected apps die?
  * ``compute_volume`` — applications per week, for a small line chart.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date as date_cls
from datetime import timedelta
from typing import Dict, List, Optional

from sqlalchemy import asc, select

from db.models import (
    PIPELINE_STAGES,
    TERMINAL_NEGATIVE_STAGES,
    Application,
    ApplicationStage,
)
from db.session import get_session

# Stages we treat as "ever reached the offer stage".
_OFFER_KINDS = {"offer_received", "offer_accepted"}
_ACTIVE_INTERVIEW_KINDS = {
    "recruiter_screen",
    "phone_screen",
    "technical_interview",
    "take_home",
    "onsite",
}


@dataclass
class Overview:
    total_applications: int = 0
    active: int = 0           # not in a terminal-negative state and not accepted
    in_interview: int = 0     # latest stage is an interview-ish kind
    offers_received: int = 0
    offers_accepted: int = 0
    rejected: int = 0
    withdrew: int = 0
    ghosted: int = 0
    rejection_rate: float = 0.0  # rejected / (rejected + offers_received)


@dataclass
class FunnelRow:
    stage: str
    reached: int
    conversion_from_previous: Optional[float]  # None for the first stage


@dataclass
class TimeInStage:
    from_stage: str
    to_stage: str
    average_days: float
    samples: int


@dataclass
class VerdictOutcome:
    verdict: str         # Highly Recommended / Recommended / Consider with Caution / Not Recommended / Unknown
    applications: int
    reached_offer: int
    rejected: int
    offer_rate: float    # offers / applications


@dataclass
class Dashboard:
    overview: Overview = field(default_factory=Overview)
    funnel: List[FunnelRow] = field(default_factory=list)
    time_in_stage: List[TimeInStage] = field(default_factory=list)
    verdict_outcomes: List[VerdictOutcome] = field(default_factory=list)
    rejection_stage_distribution: Dict[str, int] = field(default_factory=dict)
    volume_by_week: Dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def _load(user_id: int):
    """Pull all of the user's applications + their stages in one go.

    Returns ``[(Application, [stages_sorted])]``.
    """
    with get_session() as session:
        apps = session.execute(
            select(Application).where(Application.user_id == user_id)
        ).scalars().all()
        if not apps:
            return []
        ids = [a.id for a in apps]
        stages = session.execute(
            select(ApplicationStage)
            .where(ApplicationStage.application_id.in_(ids))
            .order_by(asc(ApplicationStage.occurred_on), asc(ApplicationStage.id))
        ).scalars().all()
        by_app: Dict[int, list] = defaultdict(list)
        for s in stages:
            by_app[s.application_id].append(s)
        # Return detached tuples so the session can close before computation.
        return [
            (
                {
                    "id": a.id,
                    "verdict": a.verdict,
                    "verdict_light": a.verdict_light,
                    "created_at": a.created_at,
                },
                [
                    {
                        "kind": s.kind,
                        "occurred_on": s.occurred_on,
                        "at_pipeline_stage": s.at_pipeline_stage,
                    }
                    for s in by_app.get(a.id, [])
                ],
            )
            for a in apps
        ]


# ---------------------------------------------------------------------------
# Individual computations (each takes the pre-loaded data)
# ---------------------------------------------------------------------------

def _stage_kinds_seen(stages: list) -> set:
    return {s["kind"] for s in stages}


def _is_terminal_negative(kinds: set) -> bool:
    return bool(kinds & set(TERMINAL_NEGATIVE_STAGES))


def _reached_offer(kinds: set) -> bool:
    return bool(kinds & _OFFER_KINDS)


def compute_overview(data) -> Overview:
    o = Overview(total_applications=len(data))
    for _, stages in data:
        kinds = _stage_kinds_seen(stages)
        if "offer_accepted" in kinds:
            o.offers_accepted += 1
            o.offers_received += 1
            continue
        if "offer_received" in kinds and not _is_terminal_negative(kinds):
            o.offers_received += 1
        if "rejected" in kinds:
            o.rejected += 1
        if "withdrew" in kinds:
            o.withdrew += 1
        if "ghosted" in kinds:
            o.ghosted += 1
        # Active = no terminal-negative AND not yet accepted.
        if not _is_terminal_negative(kinds) and "offer_accepted" not in kinds:
            o.active += 1
        # In interview = latest stage is an interview-ish kind, and not terminal.
        if stages and not _is_terminal_negative(kinds):
            latest_kind = stages[-1]["kind"]
            if latest_kind in _ACTIVE_INTERVIEW_KINDS:
                o.in_interview += 1
    denom = o.rejected + o.offers_received
    o.rejection_rate = round(o.rejected / denom, 3) if denom else 0.0
    return o


def compute_funnel(data) -> List[FunnelRow]:
    """Build the funnel from non-zero pipeline stages only.

    Empty pipeline stages (no one reached them) are skipped so the displayed
    chart and conversion rates reflect real signal: e.g. if nobody logged a
    ``recruiter_screen`` but two logged ``phone_screen``, phone_screen's
    conversion is computed against ``applied``, not against 0.
    """
    counts: Counter = Counter()
    for _, stages in data:
        kinds = _stage_kinds_seen(stages)
        for stage in PIPELINE_STAGES:
            if stage in kinds:
                counts[stage] += 1
    rows: List[FunnelRow] = []
    prev: Optional[int] = None
    for stage in PIPELINE_STAGES:
        reached = counts[stage]
        if reached == 0:
            continue
        rate = None if prev is None else round(reached / prev, 3) if prev else 0.0
        rows.append(FunnelRow(stage=stage, reached=reached, conversion_from_previous=rate))
        prev = reached
    return rows


def compute_time_in_stage(data) -> List[TimeInStage]:
    """Average days between the actual consecutive pipeline events per app.

    Pairs are formed from *each application's own chronological pipeline
    events* rather than from fixed adjacent pairs of ``PIPELINE_STAGES`` —
    so an app that goes ``applied → phone_screen → onsite`` contributes a
    ``phone_screen → onsite`` sample, even though the canonical funnel has
    ``technical_interview`` in between.
    """
    pairs: Dict[tuple, List[int]] = defaultdict(list)
    pipeline_set = set(PIPELINE_STAGES)
    for _, stages in data:
        # First occurrence per kind, chronological order.
        first_seen: Dict[str, date_cls] = {}
        for s in stages:
            if s["kind"] in pipeline_set:
                first_seen.setdefault(s["kind"], s["occurred_on"])
        sorted_events = sorted(first_seen.items(), key=lambda kv: kv[1])
        for i in range(len(sorted_events) - 1):
            (ka, da), (kb, db) = sorted_events[i], sorted_events[i + 1]
            delta = (db - da).days
            if delta >= 0:
                pairs[(ka, kb)].append(delta)
    out: List[TimeInStage] = []
    for (a, b), deltas in pairs.items():
        out.append(
            TimeInStage(
                from_stage=a,
                to_stage=b,
                average_days=round(sum(deltas) / len(deltas), 1),
                samples=len(deltas),
            )
        )
    out.sort(key=lambda t: (PIPELINE_STAGES.index(t.from_stage), t.to_stage))
    return out


def compute_verdict_outcome(data) -> List[VerdictOutcome]:
    buckets: Dict[str, Dict[str, int]] = defaultdict(lambda: {"apps": 0, "offer": 0, "rejected": 0})
    for app, stages in data:
        verdict = (app["verdict"] or "Unknown").strip() or "Unknown"
        kinds = _stage_kinds_seen(stages)
        b = buckets[verdict]
        b["apps"] += 1
        if _reached_offer(kinds):
            b["offer"] += 1
        if "rejected" in kinds:
            b["rejected"] += 1
    out: List[VerdictOutcome] = []
    # Stable order: known verdicts first, then anything else alphabetical.
    order = [
        "Highly Recommended", "Recommended", "Consider with Caution",
        "Not Recommended", "Unknown",
    ]
    seen = set()
    for v in order:
        if v in buckets:
            seen.add(v)
            b = buckets[v]
            out.append(
                VerdictOutcome(
                    verdict=v,
                    applications=b["apps"],
                    reached_offer=b["offer"],
                    rejected=b["rejected"],
                    offer_rate=round(b["offer"] / b["apps"], 3) if b["apps"] else 0.0,
                )
            )
    for v in sorted(buckets.keys()):
        if v not in seen:
            b = buckets[v]
            out.append(
                VerdictOutcome(
                    verdict=v,
                    applications=b["apps"],
                    reached_offer=b["offer"],
                    rejected=b["rejected"],
                    offer_rate=round(b["offer"] / b["apps"], 3) if b["apps"] else 0.0,
                )
            )
    return out


def compute_rejection_stage(data) -> Dict[str, int]:
    """For each pipeline stage, count rejections that occurred at that stage.

    Uses ``at_pipeline_stage`` when present, else infers from the latest
    non-terminal stage the application reached.
    """
    counts: Counter = Counter()
    for _, stages in data:
        rejected_stage = next((s for s in stages if s["kind"] == "rejected"), None)
        if rejected_stage is None:
            continue
        bucket = rejected_stage.get("at_pipeline_stage")
        if not bucket:
            # Latest non-terminal pipeline stage before the rejection.
            prior = [
                s["kind"] for s in stages
                if s["kind"] in PIPELINE_STAGES and s["occurred_on"] <= rejected_stage["occurred_on"]
            ]
            bucket = prior[-1] if prior else "applied"
        counts[bucket] += 1
    # Return a dict in canonical funnel order so the bar chart reads left-to-right.
    return {stage: counts.get(stage, 0) for stage in PIPELINE_STAGES if counts.get(stage)}


def compute_volume(data) -> Dict[str, int]:
    """Applications saved per ISO week (label = Monday of that week)."""
    counts: Counter = Counter()
    for app, _ in data:
        created = app["created_at"]
        if created is None:
            continue
        d = created.date() if hasattr(created, "date") else created
        # Snap to Monday of the ISO week so the bucket labels are dates.
        monday = d - timedelta(days=d.weekday())
        counts[monday.isoformat()] += 1
    return dict(sorted(counts.items()))


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def compute_dashboard(user_id: int) -> Dashboard:
    data = _load(user_id)
    return Dashboard(
        overview=compute_overview(data),
        funnel=compute_funnel(data),
        time_in_stage=compute_time_in_stage(data),
        verdict_outcomes=compute_verdict_outcome(data),
        rejection_stage_distribution=compute_rejection_stage(data),
        volume_by_week=compute_volume(data),
    )
