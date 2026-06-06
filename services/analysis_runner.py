"""Unified entry point for running an analysis sync or async.

Callers don't need to know whether a Celery broker is configured:
  * ``async_enabled()`` reports whether a worker queue is available.
  * ``enqueue_analysis(...)`` dispatches to Celery and returns a task id.
  * ``run_analysis_sync(...)`` runs in-process (the default, with live
    progress callbacks for the Streamlit UI).
  * ``get_async_result(task_id)`` polls a queued task.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from services.rate_limit import ANALYSIS_LIMITER, RateLimitExceeded
from worker.tasks import analyze_payload

logger = logging.getLogger(__name__)


def check_user_quota(user_id: Optional[int]) -> None:
    """Enforce the per-user analysis quota. ``None`` skips (bot/anon flow)."""
    if user_id is None:
        return
    decision = ANALYSIS_LIMITER.check(str(user_id))
    if not decision.allowed:
        raise RateLimitExceeded(decision.retry_after)


def async_enabled() -> bool:
    """True when a Celery broker + worker task are available."""
    try:
        from worker.tasks import analyze_task
    except Exception:  # noqa: BLE001
        return False
    return analyze_task is not None


def run_analysis_sync(
    job_posting: str,
    manual_inputs: Optional[dict] = None,
    model: str = "detailed",
    resume_text: Optional[str] = None,
    progress_callback: Optional[Callable] = None,
) -> dict:
    """Run analysis in-process. Used by the interactive Streamlit flow."""
    from agents.orchestrator import run_analysis

    return run_analysis(
        job_posting,
        manual_inputs=manual_inputs,
        model=model,
        progress_callback=progress_callback,
        resume_text=resume_text,
    )


def enqueue_analysis(
    job_posting: str,
    manual_inputs: Optional[dict] = None,
    model: str = "detailed",
    resume_text: Optional[str] = None,
) -> Optional[str]:
    """Dispatch to the Celery worker. Returns a task id, or None if async is off."""
    if not async_enabled():
        return None
    from worker.tasks import analyze_task

    async_result = analyze_task.delay(
        job_posting,
        manual_inputs=manual_inputs,
        model=model,
        resume_text=resume_text,
    )
    return async_result.id


def get_async_result(task_id: str):
    """Return ``(state, result_or_none)`` for a queued task."""
    from worker.celery_app import app

    if app is None:
        return ("UNAVAILABLE", None)
    res = app.AsyncResult(task_id)
    return (res.state, res.result if res.ready() else None)


# Convenience: pick the best execution mode automatically.
def submit(
    job_posting: str,
    manual_inputs: Optional[dict] = None,
    model: str = "detailed",
    resume_text: Optional[str] = None,
) -> dict:
    """Enqueue if a worker is available (returns {'task_id': ...}), else run
    synchronously (returns the full result dict)."""
    task_id = enqueue_analysis(job_posting, manual_inputs, model, resume_text)
    if task_id is not None:
        return {"task_id": task_id, "mode": "async"}
    result = run_analysis_sync(job_posting, manual_inputs, model, resume_text)
    return {"result": result, "mode": "sync"}
