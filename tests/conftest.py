"""Shared fixtures. All tests run offline: fake LLM, fake embedder, mocked HTTP.

DB tests (marker `db`) use TEST_DATABASE_URL (default: the local dev server from scripts/devdb.py,
or the Postgres service in CI) and are skipped if it is unreachable.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
TEST_DB = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://postgres@127.0.0.1:54329/joblens_test")

os.environ.update(
    {
        "LLM_PROVIDER": "fake",
        "LLM_API_KEY": "",
        "JUDGE_API_KEY": "",
        "GEMINI_API_KEY": "",
        "EMBEDDER": "fake",
        "DATABASE_URL": TEST_DB,
        "ADMIN_API_KEY": "test-admin-key",
        "DEMO_LIMITS": "false",  # many tests call paste/chat repeatedly; tests/test_limits.py turns limits on
    }
)

TABLES = [
    "eval_runs", "llm_cache", "user_memory", "chat_messages", "chat_sessions", "explanations",
    "usage_counts", "profiles", "trust_scores", "job_embeddings", "job_facts", "jobs",
]


@pytest.fixture(autouse=True)
def _fresh_settings() -> Iterator[None]:
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def db_url() -> str:
    """Migrated test database URL, or skip. With REQUIRE_TEST_DB=1 (set in CI) an unreachable database fails the
    DB tests instead of skipping them, so a broken Postgres service cannot turn the run green."""
    try:
        eng = create_engine(TEST_DB)
        with eng.connect() as c:
            c.execute(text("SELECT 1"))
        eng.dispose()
    except Exception as e:  # noqa: BLE001
        msg = f"test database not reachable at {TEST_DB}: {type(e).__name__}"
        if os.environ.get("REQUIRE_TEST_DB") == "1":
            pytest.fail(msg + " (REQUIRE_TEST_DB=1)")
        pytest.skip(msg)
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["url"] = TEST_DB
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    return TEST_DB


@pytest.fixture
def sf(db_url: str) -> Iterator[sessionmaker[Session]]:
    """Session factory on a truncated test DB."""
    from app.db import get_engine

    eng = get_engine(db_url)
    with eng.begin() as c:
        c.execute(text("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY CASCADE"))
    yield sessionmaker(bind=eng, expire_on_commit=False)


@pytest.fixture
def session(sf: sessionmaker[Session]) -> Iterator[Session]:
    with sf() as s:
        yield s


@pytest.fixture(autouse=True)
def _no_stored_classifier(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never load models/scam_clf.joblib (absent in CI); tests that need one pass a model explicitly."""
    import app.trust.classifier as clf

    monkeypatch.setattr(clf, "load_model", lambda path=None: None)
