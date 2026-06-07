"""Background analyses — queue an analysis, walk away, come back to it later.

The web UI submits long pipelines to Celery and persists the (user_id, task_id)
pairing here. Polling ``refresh_state`` walks each pending row against the
Celery result backend and writes back the terminal state + result so future
polls don't keep hammering the broker.

All operations are ownership-scoped — cross-user reads / mutations raise.
``submit_background_analysis`` returns ``None`` when no broker is configured,
letting callers fall back to the existing synchronous path cleanly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, List, Optional

from sqlalchemy import desc, select

from db.models import BACKGROUND_TERMINAL_STATES, BackgroundAnalysis
from db.session import get_session
from services.analysis_runner import (
    async_enabled,
    enqueue_analysis,
    get_async_result,
)

logger = logging.getLogger(__name__)


class BackgroundAnalysisError(ValueError):
    """User-facing failure (not found / cross-user / etc.)."""


@dataclass
class BackgroundAnalysisRecord:
    id: int
    user_id: int
    task_id: str
    title: str
    state: str
    inputs_summary: Optional[str]
    result_json: Optional[dict]
    error_message: Optional[str]
    created_at: datetime
    completed_at: Optional[datetime]


def _to_record(row: BackgroundAnalysis) -> BackgroundAnalysisRecord:
    return BackgroundAnalysisRecord(
        id=row.id,
        user_id=row.user_id,
        task_id=row.task_id,
        title=row.title,
        state=row.state,
        inputs_summary=row.inputs_summary,
        result_json=row.result_json,
        error_message=row.error_message,
        created_at=row.created_at,
        completed_at=row.completed_at,
    )


# ---------------------------------------------------------------------------
# Submit
# ---------------------------------------------------------------------------

def submit_background_analysis(
    user_id: int,
    job_posting: str,
    manual_inputs: Optional[dict] = None,
    model: str = "detailed",
    resume_text: Optional[str] = None,
    title: Optional[str] = None,
) -> Optional[BackgroundAnalysisRecord]:
    """Enqueue the analysis on Celery and persist a tracking row.

    Returns ``None`` when ``async_enabled()`` is False so the caller knows to
    fall back to the synchronous path. The job posting itself is NOT stored on
    the row — it can be large and we already pay for it by passing it through
    the broker. Inputs summary is stored for the UI's display.
    """
    if not async_enabled():
        return None

    task_id = enqueue_analysis(
        job_posting,
        manual_inputs=manual_inputs,
        model=model,
        resume_text=resume_text,
        user_id=user_id,
    )
    if task_id is None:
        return None

    summary = _summarize_inputs(manual_inputs)
    label = title or _derive_title(manual_inputs)

    with get_session() as session:
        row = BackgroundAnalysis(
            user_id=user_id,
            task_id=task_id,
            title=label,
            state="PENDING",
            inputs_summary=summary,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return _to_record(row)


def _summarize_inputs(manual: Optional[dict]) -> str:
    if not manual:
        return ""
    bits = []
    for key in ("company_name", "job_title", "location"):
        val = (manual.get(key) or "").strip()
        if val:
            bits.append(val)
    return " · ".join(bits)


def _derive_title(manual: Optional[dict]) -> str:
    manual = manual or {}
    title = (manual.get("job_title") or "Untitled analysis").strip()
    company = (manual.get("company_name") or "").strip()
    return f"{title} @ {company}" if company else title


# ---------------------------------------------------------------------------
# List / read
# ---------------------------------------------------------------------------

def list_for_user(user_id: int) -> List[BackgroundAnalysisRecord]:
    """Newest first."""
    with get_session() as session:
        rows = session.execute(
            select(BackgroundAnalysis)
            .where(BackgroundAnalysis.user_id == user_id)
            .order_by(desc(BackgroundAnalysis.created_at))
        ).scalars().all()
        return [_to_record(r) for r in rows]


def get(user_id: int, analysis_id: int) -> BackgroundAnalysisRecord:
    with get_session() as session:
        row = session.get(BackgroundAnalysis, analysis_id)
        if row is None or row.user_id != user_id:
            raise BackgroundAnalysisError("Background analysis not found.")
        return _to_record(row)


# ---------------------------------------------------------------------------
# Poll Celery + persist terminal state
# ---------------------------------------------------------------------------

def refresh_state(user_id: int, analysis_id: int) -> BackgroundAnalysisRecord:
    """Ask Celery for the task's current state; persist if terminal.

    Already-terminal rows short-circuit without hitting the broker. On
    ``SUCCESS`` we cache the result blob on the row; on ``FAILURE`` we cache
    the error string. Either way, future ``refresh_state`` calls become
    pure reads.
    """
    with get_session() as session:
        row = session.get(BackgroundAnalysis, analysis_id)
        if row is None or row.user_id != user_id:
            raise BackgroundAnalysisError("Background analysis not found.")
        if row.state in BACKGROUND_TERMINAL_STATES:
            return _to_record(row)

        state, payload = get_async_result(row.task_id)
        if state == "UNAVAILABLE":
            # No broker configured (anymore) — leave the row as it is.
            return _to_record(row)

        row.state = state
        if state == "SUCCESS":
            row.result_json = _clean_result(payload)
            row.completed_at = datetime.utcnow()
        elif state in ("FAILURE", "REVOKED"):
            row.error_message = _stringify_error(payload)
            row.completed_at = datetime.utcnow()
        session.commit()
        session.refresh(row)
        return _to_record(row)


def refresh_all_pending(user_id: int) -> List[BackgroundAnalysisRecord]:
    """Refresh every non-terminal row for one user. UI poll uses this."""
    out: List[BackgroundAnalysisRecord] = []
    for rec in list_for_user(user_id):
        if rec.state in BACKGROUND_TERMINAL_STATES:
            out.append(rec)
            continue
        try:
            out.append(refresh_state(user_id, rec.id))
        except BackgroundAnalysisError:
            out.append(rec)
    return out


def _clean_result(result: Any) -> dict:
    """Drop non-JSON-serialisable bits before caching."""
    if not isinstance(result, dict):
        return {"raw": str(result)}
    # The orchestrator may still leave a progress_callback in the dict for
    # the in-process path. Celery would have stripped it; defend in depth.
    cleaned = {k: v for k, v in result.items() if not callable(v)}
    return cleaned


def _stringify_error(payload: Any) -> str:
    if payload is None:
        return "Task failed (no error details from broker)."
    return str(payload)[:1000]


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

def delete(user_id: int, analysis_id: int) -> None:
    with get_session() as session:
        row = session.get(BackgroundAnalysis, analysis_id)
        if row is None or row.user_id != user_id:
            raise BackgroundAnalysisError("Background analysis not found.")
        session.delete(row)
        session.commit()


def cleanup_terminal_older_than(days: int) -> int:
    """Sweep cache: delete terminal rows older than ``days``. Returns count."""
    from datetime import timedelta

    cutoff = datetime.utcnow() - timedelta(days=days)
    with get_session() as session:
        rows = session.execute(
            select(BackgroundAnalysis)
            .where(BackgroundAnalysis.state.in_(BACKGROUND_TERMINAL_STATES))
            .where(BackgroundAnalysis.completed_at < cutoff)
        ).scalars().all()
        for r in rows:
            session.delete(r)
        session.commit()
        return len(rows)
