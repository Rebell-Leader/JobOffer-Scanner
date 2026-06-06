"""``timed_block`` — context manager that times an operation and emits one
log line plus a histogram observation when it exits.

Usage::

    from utils.timing import timed_block

    with timed_block("llm.request", tags={"provider": "openai", "model": "gpt-4o-mini"}):
        client.chat.completions.create(...)

On success it logs at INFO; on exception it logs at WARNING with the exception
type so an operator can grep for slow + failing calls in one query. The
histogram observation records duration in milliseconds. The error tag is added
to the metric automatically so error rates per operation are derivable from a
snapshot.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Dict, Optional

from utils import metrics

logger = logging.getLogger("timing")


@contextmanager
def timed_block(
    name: str,
    tags: Optional[Dict[str, str]] = None,
    log_level: int = logging.INFO,
):
    """Time a block of code; emit a log line + a histogram observation on exit.

    ``tags`` are attached to both the log line (as ``extra``) and the metric.
    The exit always records, even on exception — exceptions get an ``error=1``
    tag on the metric and an ``ERROR`` log line.
    """
    started = time.monotonic()
    tag_dict = dict(tags or {})
    error_type: Optional[str] = None
    try:
        yield
    except BaseException as exc:  # noqa: BLE001 - re-raised after recording
        error_type = type(exc).__name__
        raise
    finally:
        duration_ms = (time.monotonic() - started) * 1000.0
        # Histogram for normal latency, plus a counter for failures.
        metric_tags = dict(tag_dict)
        if error_type:
            metric_tags["error"] = error_type
            metrics.increment(f"{name}.errors", tags=tag_dict)
        metrics.observe(f"{name}.duration_ms", duration_ms, tags=metric_tags)
        metrics.increment(f"{name}.count", tags=tag_dict)
        log_payload = {
            "op": name,
            "duration_ms": round(duration_ms, 2),
            **tag_dict,
        }
        if error_type:
            log_payload["error"] = error_type
            logger.log(logging.ERROR, f"op_failed: {name}", extra=log_payload)
        else:
            logger.log(log_level, f"op_done: {name}", extra=log_payload)
