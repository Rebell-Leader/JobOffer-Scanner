"""Database engine & session factory.

Defaults to SQLite at ``./data/joboffer.db`` when ``DATABASE_URL`` is unset, so
the app works out of the box. Set ``DATABASE_URL=postgresql://...`` to point
at Postgres.

Schema init is idempotent (``Base.metadata.create_all``) — fine for SQLite and
early Postgres. Alembic migrations come later when the schema starts evolving.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from db.models import Base
from utils.env import env_bool


def _enable_sqlite_fk(engine: Engine) -> None:
    """Turn on ``PRAGMA foreign_keys`` for SQLite connections.

    SQLite ignores ``ON DELETE CASCADE`` / ``SET NULL`` unless foreign-key
    enforcement is enabled per-connection. Without this, account deletion
    (which relies on cascade) would orphan child rows on SQLite while working
    on Postgres — a dev/prod behaviour split. This makes them identical.
    """
    if not str(engine.url).startswith("sqlite"):
        return

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_connection, _record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

logger = logging.getLogger(__name__)

_DEFAULT_SQLITE_PATH = Path(os.getenv("SQLITE_PATH", "./data/joboffer.db"))

_engine: Optional[Engine] = None
_SessionLocal: Optional[sessionmaker] = None
_initialized = False


def _resolve_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    _DEFAULT_SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{_DEFAULT_SQLITE_PATH.as_posix()}"


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        url = _resolve_database_url()
        # SQLite needs ``check_same_thread=False`` so Streamlit's thread pool
        # can share the connection.
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engine = create_engine(url, connect_args=connect_args, future=True)
        _enable_sqlite_fk(_engine)
        logger.info("DB engine created for %s", url.split("://", 1)[0])
    return _engine


def init_db() -> None:
    """Initialize the schema. Safe to call repeatedly.

    By default uses ``Base.metadata.create_all`` for zero-config setups
    (SQLite, first run). Set ``USE_ALEMBIC=1`` in environments where you've
    adopted the migrations under ``migrations/`` — then this just no-ops and
    expects the operator to have run ``alembic upgrade head``.
    """
    global _initialized
    if _initialized:
        return
    if env_bool("USE_ALEMBIC"):
        logger.info("USE_ALEMBIC=1 — skipping create_all; run `alembic upgrade head`.")
    else:
        Base.metadata.create_all(get_engine())
    _initialized = True


def get_session() -> Session:
    """Return a new ORM session. Caller is responsible for closing it."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    init_db()
    return _SessionLocal()


def reset_engine_for_testing(url: str) -> None:
    """Test helper: swap to an in-memory SQLite (or any URL) and reinit.

    Uses ``StaticPool`` so every SQLAlchemy connection in the test process
    reuses the same underlying SQLite connection. Without this, code paths
    that hop threads (``asyncio.to_thread``, Streamlit reruns, etc.) get a
    fresh ``:memory:`` database with no tables.
    """
    from sqlalchemy.pool import StaticPool

    global _engine, _SessionLocal, _initialized
    _engine = create_engine(
        url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    _enable_sqlite_fk(_engine)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    _initialized = False
    init_db()
