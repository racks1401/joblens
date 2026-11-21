"""Keyword retrieval: Postgres full-text search (websearch_to_tsquery + ts_rank_cd). BM25-like, not BM25."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

SQL = text("""
SELECT id, ts_rank_cd(fts, q) AS score
FROM jobs, websearch_to_tsquery('english', :q) AS q
WHERE is_active AND (in_scope OR :all_jobs) AND fts @@ q
  AND EXISTS (SELECT 1 FROM job_facts f WHERE f.job_id = jobs.id AND f.valid)
ORDER BY score DESC, id
LIMIT :limit
""")


def keyword_search(s: Session, q: str, limit: int = 50, include_out_of_scope: bool = False) -> list[tuple[int, float]]:
    """Active in-scope (fresher-relevant + India-eligible) jobs unless include_out_of_scope; jobs matching the query: [(job_id, ts_rank_cd)].
    Jobs without valid extracted facts are left out until extraction has run."""
    if not q.strip():
        return []
    return [(int(r.id), float(r.score)) for r in s.execute(SQL, {"q": q, "limit": limit, "all_jobs": include_out_of_scope})]
