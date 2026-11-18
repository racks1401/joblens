import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.llm.cache import DBCache
from app.models import Job

pytestmark = pytest.mark.db


def test_all_tables_exist(session: Session) -> None:
    rows = session.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'")).scalars().all()
    for t in ["jobs", "job_facts", "job_embeddings", "trust_scores", "profiles", "explanations",
              "chat_sessions", "chat_messages", "user_memory", "llm_cache", "eval_runs"]:
        assert t in rows


def test_fts_generated_column(session: Session) -> None:
    session.add(Job(source="paste", source_job_id="x", company="Acme", title="Python Developer",
                    description_text="Build REST APIs with FastAPI", content_hash="h"))
    session.commit()
    n = session.execute(
        text("SELECT count(*) FROM jobs WHERE fts @@ websearch_to_tsquery('english', 'fastapi')")
    ).scalar()
    assert n == 1


def test_db_cache_roundtrip(sf) -> None:  # type: ignore[no-untyped-def]
    c = DBCache(sf)
    assert c.get("k") is None
    c.put("k", "m", "v1", {"content": "hi"}, 3, 4)
    c.put("k", "m", "v1", {"content": "ignored"}, 3, 4)  # on conflict do nothing
    assert c.get("k") == {"content": "hi"}
    assert c.tokens_today() == 7
