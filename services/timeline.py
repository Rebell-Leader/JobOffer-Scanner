"""Per-application + cross-application timeline data, ready for Altair.

This module is intentionally chart-agnostic — it returns plain
dataclasses / list[dict] that the UI layer feeds into an ``altair.Chart``.
Keeping the SQL and the visualization concerns split lets us swap in a
different chart library without touching the data path.

Two builders:

  * ``per_application_timeline`` — one application's pipeline events on a date
    axis. The earliest stage anchors the left edge; the latest anchors the
    right edge. Used inside each saved-application expander.
  * ``cross_application_swimlane`` — every saved application as a row,
    with its stages plotted on a shared date axis. Surfaces velocity /
    funnel-drop patterns at a glance. Used on the Analytics tab.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_cls
from typing import Dict, List, Optional

from sqlalchemy import asc, select

from db.models import (
    PIPELINE_STAGES,
    Application,
    ApplicationStage,
)
from db.session import get_session

# Color hexes chosen to read on both light and dark Streamlit themes.
# Pipeline stages: cooler -> warmer as the user advances; terminals stand out.
STAGE_COLORS: Dict[str, str] = {
    "applied":             "#6c757d",  # neutral grey
    "recruiter_screen":    "#5dade2",
    "phone_screen":        "#5dade2",
    "technical_interview": "#3498db",
    "take_home":           "#9b59b6",
    "onsite":              "#1f618d",
    "offer_received":      "#f1c40f",
    "offer_accepted":      "#27ae60",  # green
    "rejected":            "#c0392b",  # red
    "withdrew":            "#7f8c8d",
    "ghosted":             "#34495e",
}


@dataclass(frozen=True)
class TimelinePoint:
    """One event on a timeline chart."""

    application_id: int
    application_label: str   # "ML Engineer @ Stripe" — for swimlane y-axis
    kind: str
    occurred_on: date_cls
    color: str
    pipeline_index: int      # -1 for non-pipeline stages; sort key for per-app chart
    notes: Optional[str]


# ---------------------------------------------------------------------------
# Per-application timeline
# ---------------------------------------------------------------------------

def per_application_timeline(user_id: int, application_id: int) -> List[TimelinePoint]:
    """Chronological stage events for one application.

    Ownership-checked: returns an empty list if the application is not the
    user's. Pipeline stages keep their PIPELINE_STAGES index so the chart can
    sort them consistently along the Y axis; non-pipeline stages get -1 and
    sort to the bottom.
    """
    with get_session() as session:
        app = session.get(Application, application_id)
        if app is None or app.user_id != user_id:
            return []
        label = _label(app)
        stages = session.execute(
            select(ApplicationStage)
            .where(ApplicationStage.application_id == application_id)
            .order_by(asc(ApplicationStage.occurred_on), asc(ApplicationStage.id))
        ).scalars().all()
        return [_to_point(app.id, label, s) for s in stages]


# ---------------------------------------------------------------------------
# Cross-application swimlane
# ---------------------------------------------------------------------------

def cross_application_swimlane(user_id: int) -> List[TimelinePoint]:
    """All the user's applications as one combined dataset.

    Suitable for Altair with a Y encoding on ``application_label`` and an X
    encoding on ``occurred_on`` — each row of the resulting chart becomes a
    horizontal "lane" with the stages of that application plotted on it.
    Empty when the user has no applications or no stages.
    """
    with get_session() as session:
        apps = session.execute(
            select(Application).where(Application.user_id == user_id)
        ).scalars().all()
        if not apps:
            return []
        labels = {a.id: _label(a) for a in apps}
        ids = list(labels)
        rows = session.execute(
            select(ApplicationStage)
            .where(ApplicationStage.application_id.in_(ids))
            .order_by(asc(ApplicationStage.occurred_on), asc(ApplicationStage.id))
        ).scalars().all()
        return [_to_point(s.application_id, labels[s.application_id], s) for s in rows]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _label(app: Application) -> str:
    return f"{app.job_title} @ {app.company_name}"


def _to_point(application_id: int, label: str, stage: ApplicationStage) -> TimelinePoint:
    kind = stage.kind
    try:
        index = PIPELINE_STAGES.index(kind)
    except ValueError:
        index = -1
    return TimelinePoint(
        application_id=application_id,
        application_label=label,
        kind=kind,
        occurred_on=stage.occurred_on,
        color=STAGE_COLORS.get(kind, "#6c757d"),
        pipeline_index=index,
        notes=stage.notes,
    )


def points_to_records(points: List[TimelinePoint]) -> List[dict]:
    """Plain-dict view of a list of points — useful for pandas.DataFrame()."""
    return [
        {
            "application_id": p.application_id,
            "application_label": p.application_label,
            "kind": p.kind,
            "occurred_on": p.occurred_on,
            "color": p.color,
            "pipeline_index": p.pipeline_index,
            "notes": p.notes or "",
        }
        for p in points
    ]
