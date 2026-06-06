"""Application tracking — save, list, update, delete.

Every saved analysis lives as one ``Application`` row owned by the user that
created it. We intentionally store the entire analysis blob (``analysis_json``)
so the historical view doesn't need a fresh LLM call to render.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, List, Optional

from sqlalchemy import desc, select

from db.models import APPLICATION_STATUSES, Application
from db.session import get_session


class ApplicationError(ValueError):
    """User-facing failure (invalid status, ownership violation, etc.)."""


@dataclass
class ApplicationRecord:
    id: int
    company_name: str
    job_title: str
    location: Optional[str]
    status: str
    verdict: Optional[str]
    verdict_light: Optional[str]
    ats_score: Optional[int]
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime
    analysis_json: dict


def _to_record(app: Application) -> ApplicationRecord:
    return ApplicationRecord(
        id=app.id,
        company_name=app.company_name,
        job_title=app.job_title,
        location=app.location,
        status=app.status,
        verdict=app.verdict,
        verdict_light=app.verdict_light,
        ats_score=app.ats_score,
        notes=app.notes,
        created_at=app.created_at,
        updated_at=app.updated_at,
        analysis_json=app.analysis_json or {},
    )


def save_analysis(
    user_id: int,
    manual_inputs: dict,
    analysis_result: dict,
    status: str = "saved",
    notes: Optional[str] = None,
) -> ApplicationRecord:
    """Persist an analysis result as a new application row."""
    if status not in APPLICATION_STATUSES:
        raise ApplicationError(f"Unknown status {status!r}.")

    verdict_data = analysis_result.get("verdict") or {}
    resume_data = analysis_result.get("resume_analysis") or {}

    company_name = (manual_inputs.get("company_name") or "").strip()
    job_title = (manual_inputs.get("job_title") or "").strip()
    if not company_name or not job_title:
        raise ApplicationError("Cannot save: company name and job title are required.")

    with get_session() as session:
        app = Application(
            user_id=user_id,
            company_name=company_name,
            job_title=job_title,
            location=(manual_inputs.get("location") or "").strip() or None,
            status=status,
            verdict=verdict_data.get("verdict"),
            verdict_light=verdict_data.get("light"),
            ats_score=resume_data.get("ats_score"),
            notes=notes,
            analysis_json=_serializable(analysis_result),
        )
        session.add(app)
        session.commit()
        session.refresh(app)
        return _to_record(app)


def list_applications(user_id: int) -> List[ApplicationRecord]:
    with get_session() as session:
        rows = session.execute(
            select(Application)
            .where(Application.user_id == user_id)
            .order_by(desc(Application.created_at))
        ).scalars().all()
        return [_to_record(r) for r in rows]


def get_application(user_id: int, application_id: int) -> ApplicationRecord:
    with get_session() as session:
        app = session.get(Application, application_id)
        if app is None or app.user_id != user_id:
            raise ApplicationError("Application not found.")
        return _to_record(app)


def update_status(
    user_id: int,
    application_id: int,
    status: Optional[str] = None,
    notes: Optional[str] = None,
) -> ApplicationRecord:
    if status is not None and status not in APPLICATION_STATUSES:
        raise ApplicationError(f"Unknown status {status!r}.")
    with get_session() as session:
        app = session.get(Application, application_id)
        if app is None or app.user_id != user_id:
            raise ApplicationError("Application not found.")
        if status is not None:
            app.status = status
        if notes is not None:
            app.notes = notes
        session.commit()
        session.refresh(app)
        return _to_record(app)


def delete_application(user_id: int, application_id: int) -> None:
    with get_session() as session:
        app = session.get(Application, application_id)
        if app is None or app.user_id != user_id:
            raise ApplicationError("Application not found.")
        session.delete(app)
        session.commit()


def _serializable(value: Any) -> Any:
    """Strip non-JSON-serializable bits (e.g. progress_callback) recursively."""
    if isinstance(value, dict):
        return {k: _serializable(v) for k, v in value.items() if not callable(v)}
    if isinstance(value, list):
        return [_serializable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    # Fallback: stringify exotic types so json.dumps won't choke.
    return str(value)
