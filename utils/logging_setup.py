"""Structured logging for the whole stack.

Set ``LOG_FORMAT=json`` for machine-readable output (Datadog / CloudWatch /
Loki / etc.); anything else (or unset) uses a clean human-readable line.
``LOG_LEVEL`` sets the root level, default ``INFO``.

A contextvar ``_request_id`` lets us thread a correlation ID through nested
log calls without passing it as an argument. Use ``with request_context()``
around a request handler (or call ``set_request_id`` / ``clear_request_id``)
and every log line emitted inside that scope carries ``request_id``.

Configuration is idempotent (``configure`` once at process start; calling it
again from tests / Streamlit reruns is a no-op).
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import secrets
import sys
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

# Reserved fields the JSON formatter writes itself — extra keys via ``extra={}``
# go after these so they can't accidentally overwrite the structural shape.
_RESERVED = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message",
}

_request_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "request_id", default=None,
)


def get_request_id() -> Optional[str]:
    return _request_id.get()


def set_request_id(value: Optional[str]) -> contextvars.Token:
    return _request_id.set(value)


def clear_request_id(token: contextvars.Token) -> None:
    _request_id.reset(token)


@contextmanager
def request_context(request_id: Optional[str] = None):
    """Run a block with a request id attached to every log line inside it."""
    rid = request_id or secrets.token_hex(6)
    token = _request_id.set(rid)
    try:
        yield rid
    finally:
        _request_id.reset(token)


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

class _RequestIdFilter(logging.Filter):
    """Inject the current request_id (if any) onto every LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per log line — no fancy newlines / colors."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.utcfromtimestamp(record.created).isoformat(timespec="milliseconds") + "Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = getattr(record, "request_id", None)
        if rid:
            payload["request_id"] = rid
        # Pick up any ``extra={…}`` fields passed by the call site.
        for key, value in record.__dict__.items():
            if key in _RESERVED or key == "request_id":
                continue
            try:
                json.dumps(value)
            except TypeError:
                value = repr(value)
            payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class _PlainFormatter(logging.Formatter):
    """Human-friendly default. Adds ``[rid]`` when a request id is present."""

    def format(self, record: logging.LogRecord) -> str:
        rid = getattr(record, "request_id", None)
        rid_part = f" [{rid}]" if rid else ""
        ts = datetime.utcfromtimestamp(record.created).strftime("%H:%M:%S")
        msg = record.getMessage()
        if record.exc_info:
            msg = f"{msg}\n{self.formatException(record.exc_info)}"
        return f"{ts} {record.levelname:<5} {record.name}{rid_part}: {msg}"


# ---------------------------------------------------------------------------
# Public configuration
# ---------------------------------------------------------------------------

_configured = False


def configure(force: bool = False) -> None:
    """Set up the root logger. Safe to call repeatedly — only acts once."""
    global _configured
    if _configured and not force:
        return

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler(stream=sys.stderr)
    handler.addFilter(_RequestIdFilter())
    if os.getenv("LOG_FORMAT", "").lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(_PlainFormatter())

    root = logging.getLogger()
    root.setLevel(level)
    # Replace existing handlers so re-running tests / Streamlit reruns don't
    # multiply output.
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)

    # Quiet down extremely chatty third-party loggers at INFO.
    for chatty in ("urllib3", "httpx", "sqlalchemy.engine.Engine"):
        logging.getLogger(chatty).setLevel(max(level, logging.WARNING))

    _configured = True
