"""Application stage tracking.

Each application has a chronological list of ``ApplicationStage`` events. This
module owns the rules around them:

  * What stage kinds are valid (mirrors ``db.models.ALL_STAGE_KINDS``).
  * Auto-syncing the legacy ``Application.status`` field when stages change,
    so the existing UI keeps working without re-querying every render.
  * Ownership enforcement — a user can only mutate stages on their own apps.

Stages are the source of truth going forward; ``Application.status`` is a
denormalized cache derived from ``derive_status_from_stages``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime
from typing import List, Optional

from sqlalchemy import asc, desc, select

from db.models import (
    ALL_STAGE_KINDS,
    PIPELINE_STAGES,
    TERMINAL_NEGATIVE_STAGES,
    Application,
    ApplicationStage,
)
from db.session import get_session


class StageError(ValueError):
    """User-facing failure (invalid kind, ownership violation, etc.)."""


@dataclass
class StageRecord:
    id: int
    application_id: int
    kind: str
    occurred_on: date_cls
    notes: Optional[str]
    at_pipeline_stage: Optional[str]
    extra: dict
    created_at: datetime


def _to_record(stage: ApplicationStage) -> StageRecord:
    return StageRecord(
        id=stage.id,
        application_id=stage.application_id,
        kind=stage.kind,
        occurred_on=stage.occurred_on,
        notes=stage.notes,
        at_pipeline_stage=stage.at_pipeline_stage,
        extra=stage.extra or {},
        created_at=stage.created_at,
    )


# ---------------------------------------------------------------------------
# Stage -> Application.status mapping
# ---------------------------------------------------------------------------

# Map the latest *relevant* stage kind to the legacy status string the
# Application row exposes. Order matters: when a user has multiple stages,
# the chronologically-latest one wins.
_STATUS_FROM_KIND = {
    "applied": "applied",
    "recruiter_screen": "interviewing",
    "phone_screen": "interviewing",
    "technical_interview": "interviewing",
    "take_home": "interviewing",
    "onsite": "interviewing",
    "offer_received": "offer",
    "offer_accepted": "offer",
    "rejected": "rejected",
    "withdrew": "withdrawn",
    "ghosted": "rejected",
}


def derive_status_from_stages(stages: List[ApplicationStage]) -> Optional[str]:
    """Return the Application.status implied by these stages, or None."""
    if not stages:
        return None
    # Latest stage by date, then by insertion order as tiebreak.
    latest = sorted(stages, key=lambda s: (s.occurred_on, s.id))[-1]
    return _STATUS_FROM_KIND.get(latest.kind)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def add_stage(
    user_id: int,
    application_id: int,
    kind: str,
    occurred_on: Optional[date_cls] = None,
    notes: Optional[str] = None,
    at_pipeline_stage: Optional[str] = None,
    extra: Optional[dict] = None,
) -> StageRecord:
    """Add a stage event to an application.

    Auto-fills ``occurred_on`` to today when omitted. Refreshes the parent
    ``Application.status`` based on the new stage timeline.
    """
    if kind not in ALL_STAGE_KINDS:
        raise StageError(
            f"Unknown stage kind {kind!r}. Valid: {', '.join(ALL_STAGE_KINDS)}."
        )
    if at_pipeline_stage is not None and at_pipeline_stage not in PIPELINE_STAGES:
        raise StageError(f"Invalid at_pipeline_stage {at_pipeline_stage!r}.")
    occurred_on = occurred_on or date_cls.today()

    with get_session() as session:
        app = session.get(Application, application_id)
        if app is None or app.user_id != user_id:
            raise StageError("Application not found.")
        stage = ApplicationStage(
            application_id=application_id,
            kind=kind,
            occurred_on=occurred_on,
            notes=notes,
            at_pipeline_stage=at_pipeline_stage,
            extra=extra or None,
        )
        session.add(stage)
        # Re-derive status from ALL stages (including the new one).
        all_stages = list(app.stages) + [stage]
        new_status = derive_status_from_stages(all_stages)
        if new_status is not None:
            app.status = new_status
        session.commit()
        session.refresh(stage)
        return _to_record(stage)


def list_stages(user_id: int, application_id: int) -> List[StageRecord]:
    with get_session() as session:
        app = session.get(Application, application_id)
        if app is None or app.user_id != user_id:
            raise StageError("Application not found.")
        rows = session.execute(
            select(ApplicationStage)
            .where(ApplicationStage.application_id == application_id)
            .order_by(asc(ApplicationStage.occurred_on), asc(ApplicationStage.id))
        ).scalars().all()
        return [_to_record(r) for r in rows]


def delete_stage(user_id: int, stage_id: int) -> None:
    with get_session() as session:
        stage = session.get(ApplicationStage, stage_id)
        if stage is None:
            raise StageError("Stage not found.")
        app = session.get(Application, stage.application_id)
        if app is None or app.user_id != user_id:
            raise StageError("Stage not found.")
        session.delete(stage)
        session.flush()
        # Re-derive status from the remaining stages.
        remaining = session.execute(
            select(ApplicationStage).where(
                ApplicationStage.application_id == app.id
            )
        ).scalars().all()
        new_status = derive_status_from_stages(list(remaining))
        if new_status is not None:
            app.status = new_status
        elif app.status not in ("saved",):
            # No stages left — revert to "saved".
            app.status = "saved"
        session.commit()


def latest_stage(user_id: int, application_id: int) -> Optional[StageRecord]:
    stages = list_stages(user_id, application_id)
    return stages[-1] if stages else None


# ---------------------------------------------------------------------------
# Pre-canned quick actions for the UI ("Mark as applied", "Got offer", etc.)
# ---------------------------------------------------------------------------

QUICK_ACTIONS = [
    ("📤 Mark as applied", "applied"),
    ("📞 Got recruiter screen", "recruiter_screen"),
    ("💻 Got technical interview", "technical_interview"),
    ("🏢 Got onsite", "onsite"),
    ("🎯 Got offer", "offer_received"),
    ("✅ Accepted offer", "offer_accepted"),
    ("❌ Rejected", "rejected"),
    ("👻 Ghosted (no response)", "ghosted"),
    ("🛑 Withdrew", "withdrew"),
]


__all__ = [
    "ALL_STAGE_KINDS",
    "PIPELINE_STAGES",
    "QUICK_ACTIONS",
    "StageError",
    "StageRecord",
    "TERMINAL_NEGATIVE_STAGES",
    "add_stage",
    "delete_stage",
    "derive_status_from_stages",
    "latest_stage",
    "list_stages",
]
