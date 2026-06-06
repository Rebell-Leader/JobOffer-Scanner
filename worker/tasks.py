"""Celery task definitions.

The actual analysis logic lives in ``analyze_payload`` — a plain function that
works with or without Celery, so it's unit-testable directly. When Celery is
available it's also registered as a task.
"""

from __future__ import annotations

import logging
from typing import Optional

from agents.orchestrator import run_analysis

logger = logging.getLogger(__name__)


def analyze_payload(
    job_posting: str,
    manual_inputs: Optional[dict] = None,
    model: str = "detailed",
    resume_text: Optional[str] = None,
) -> dict:
    """Run the analysis pipeline (no progress callback — async has no live UI).

    Strips any non-serializable bits before returning so the result can cross
    the Celery result backend as JSON.
    """
    result = run_analysis(
        job_posting,
        manual_inputs=manual_inputs,
        model=model,
        progress_callback=None,
        resume_text=resume_text,
    )
    result.pop("progress_callback", None)
    return result


# Register as a Celery task only when async is configured/available.
# Import the already-built app (no factory call here) to avoid an import cycle.
try:
    from worker.celery_app import app as _celery
except Exception:  # noqa: BLE001 - never block import on celery issues
    _celery = None

if _celery is not None:
    analyze_task = _celery.task(name="worker.tasks.analyze_task")(analyze_payload)
else:  # pragma: no cover - exercised only without a broker
    analyze_task = None
