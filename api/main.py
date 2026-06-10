"""FastAPI app factory + entry point.

Run as::

    python -m api.main             # uvicorn on 127.0.0.1:8000
    uvicorn api.main:app --reload  # dev mode

Provides ``app`` at module level for ASGI servers and gunicorn/uvicorn workers
in production. Uses the same DB session + LLM client + services as the
Streamlit app — they're sister processes that share state through Postgres
(or SQLite in dev).
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Response, status

from api.billing import router as billing_router
from api.routes import router
from api.security import add_security_headers
from db.session import init_db
from utils.env import env_bool, env_int
from utils.logging_setup import configure as configure_logging

logger = logging.getLogger(__name__)

# Prometheus exposition content type (text format v0.0.4).
_PROM_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def create_app() -> FastAPI:
    configure_logging()
    from utils.config import log_effective_config
    log_effective_config()
    init_db()
    app = FastAPI(
        title="JobOffer Scanner API",
        description="Read/write access to the same data as the web UI.",
        version="1.0.0",
    )
    app.include_router(router)
    app.include_router(billing_router)
    add_security_headers(app)

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/metrics")
    def metrics(authorization: Optional[str] = Header(default=None)):
        """Prometheus scrape endpoint for THIS process's metrics.

        Off by default (returns 404) — set ``METRICS_ENABLED=1`` to expose it.
        On a public deployment also set ``METRICS_TOKEN`` and have Prometheus
        send ``Authorization: Bearer <token>`` so the metrics aren't world
        readable. Metrics are per-process; Prometheus aggregates across scraped
        instances.
        """
        if not env_bool("METRICS_ENABLED"):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        token = os.getenv("METRICS_TOKEN")
        if token and authorization != f"Bearer {token}":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
        from utils.metrics import render_prometheus

        return Response(content=render_prometheus(), media_type=_PROM_CONTENT_TYPE)

    return app


# Module-level handle so ``uvicorn api.main:app`` works without invoking
# ``create_app`` explicitly. Reusing the same instance across worker
# processes is the standard gunicorn/uvicorn pattern.
app = create_app()


def main() -> None:
    """Entry point for ``python -m api.main`` (dev convenience)."""
    import os

    import uvicorn

    uvicorn.run(
        "api.main:app",
        host=os.getenv("API_HOST", "127.0.0.1"),
        port=env_int("API_PORT", 8000),
        reload=env_bool("API_RELOAD"),
    )


if __name__ == "__main__":
    main()
