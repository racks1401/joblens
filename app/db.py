"""SQLAlchemy engine and session factory."""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


@lru_cache
def get_engine(url: str | None = None) -> Engine:
    """Create (once per URL) the engine."""
    return create_engine(url or get_settings().env.database_url, pool_pre_ping=True, future=True)


def session_factory(url: str | None = None) -> sessionmaker[Session]:
    """Session factory bound to the engine."""
    return sessionmaker(bind=get_engine(url), expire_on_commit=False, future=True)


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    s = session_factory()()
    try:
        yield s
    finally:
        s.close()


def db_status(engine: Engine | None = None) -> dict[str, object]:
    """Return {'ok': bool, 'pgvector': version|None, 'error': str|None}. Never raises."""
    try:
        eng = engine or get_engine()
        with eng.connect() as c:
            c.execute(text("SELECT 1"))
            v = c.execute(text("SELECT extversion FROM pg_extension WHERE extname='vector'")).scalar()
        return {"ok": True, "pgvector": v, "error": None}
    except Exception as e:  # noqa: BLE001 - health check must not raise
        return {"ok": False, "pgvector": None, "error": type(e).__name__}
