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
    user_id: Optional[int] = None,
) -> dict:
    """Run the analysis pipeline (no progress callback — async has no live UI).

    Strips any non-serializable bits before returning so the result can cross
    the Celery result backend as JSON. ``user_id`` opens a usage-accounting
    scope so LLM spend in the worker is attributed to the requesting user.
    """
    from services.usage import account

    with account(user_id):
        result = run_analysis(
            job_posting,
            manual_inputs=manual_inputs,
            model=model,
            progress_callback=None,
            resume_text=resume_text,
        )
    result.pop("progress_callback", None)
    return result


def _deliver_webhook(delivery_id: int) -> bool:
    """Plain function body for the webhook-delivery task (testable directly)."""
    from services.webhooks import attempt_delivery

    return attempt_delivery(delivery_id)


# Register as Celery tasks only when async is configured/available.
# Import the already-built app (no factory call here) to avoid an import cycle.
try:
    from worker.celery_app import app as _celery
except Exception:  # noqa: BLE001 - never block import on celery issues
    _celery = None

if _celery is not None:
    analyze_task = _celery.task(name="worker.tasks.analyze_task")(analyze_payload)

    @_celery.task(bind=True, name="worker.tasks.deliver_webhook_task")
    def deliver_webhook_task(self, delivery_id: int):
        """Durable webhook delivery: one POST attempt; retry with backoff.

        Retries until the row's attempt budget (``WEBHOOK_MAX_ATTEMPTS``) is
        exhausted, with exponential backoff between tries — at-least-once
        delivery rather than the best-effort daemon thread.
        """
        from services.webhooks import _max_attempts, retry_delay_for

        ok = _deliver_webhook(delivery_id)
        if ok:
            return True
        # self.request.retries is 0 on the first run, 1 on the first retry, ...
        if self.request.retries + 1 >= _max_attempts():
            logger.warning("Webhook delivery %s exhausted retries.", delivery_id)
            return False
        raise self.retry(countdown=retry_delay_for(self.request.retries))
else:  # pragma: no cover - exercised only without a broker
    analyze_task = None
    deliver_webhook_task = None
