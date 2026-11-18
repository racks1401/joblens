"""Keyword retrieval: Postgres full-text search (websearch_to_tsquery + ts_rank_cd). BM25-like, not BM25."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

SQL = text("""
SELECT id, ts_rank_cd(fts, q) AS score
FROM jobs, websearch_to_tsquery('english', :q) AS q
WHERE is_active AND fts @@ q
ORDER BY score DESC, id
LIMIT :limit
""")


def keyword_search(s: Session, q: str, limit: int = 50) -> list[tuple[int, float]]:
    """Active jobs matching the query, best first: [(job_id, ts_rank_cd)]."""
    if not q.strip():
        return []
    return [(int(r.id), float(r.score)) for r in s.execute(SQL, {"q": q, "limit": limit})]
