"""Celery application factory.

Celery is an OPTIONAL dependency. ``get_celery_app()`` returns a configured
Celery instance when ``CELERY_BROKER_URL`` is set AND celery is installed, else
``None`` — callers then fall back to running analysis in-process.

Task modules are registered via ``include=["worker.tasks"]`` rather than an
explicit import here, so the app builds without importing tasks (which import
this module back) — that avoids an import cycle. Celery imports the task module
at worker boot, once the app already exists.

A module-level ``app`` is exposed for the Celery CLI:
    celery -A worker.celery_app:app worker
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Optional

logger = logging.getLogger(__name__)


def broker_url() -> Optional[str]:
    return os.getenv("CELERY_BROKER_URL")


def result_backend() -> Optional[str]:
    # Default the result backend to the broker (Redis works for both).
    return os.getenv("CELERY_RESULT_BACKEND") or broker_url()


@lru_cache(maxsize=1)
def get_celery_app():
    """Return a configured Celery app, or None if async is unavailable."""
    url = broker_url()
    if not url:
        return None
    try:
        from celery import Celery  # lazy import
    except ImportError:
        logger.warning("CELERY_BROKER_URL set but celery not installed — running sync.")
        return None

    celery = Celery(
        "joboffer",
        broker=url,
        backend=result_backend(),
        include=["worker.tasks"],  # imported by the worker at boot, not here
    )
    celery.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        task_track_started=True,
        task_time_limit=int(os.getenv("CELERY_TASK_TIME_LIMIT", "600")),
        worker_max_tasks_per_child=50,
    )
    return celery


# Module-level handle for the Celery CLI (`-A worker.celery_app:app`).
# None when no broker is configured — the worker container always sets one.
app = get_celery_app()
