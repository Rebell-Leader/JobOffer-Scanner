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

from fastapi import FastAPI

from api.routes import router
from db.session import init_db
from utils.logging_setup import configure as configure_logging

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    configure_logging()
    init_db()
    app = FastAPI(
        title="JobOffer Scanner API",
        description="Read/write access to the same data as the web UI.",
        version="1.0.0",
    )
    app.include_router(router)

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

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
        port=int(os.getenv("API_PORT", "8000")),
        reload=os.getenv("API_RELOAD") == "1",
    )


if __name__ == "__main__":
    main()
