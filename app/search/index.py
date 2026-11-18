"""Pipeline step: set jobs.search_text (structured) and compute embeddings for every model x variant."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Job, JobEmbedding, JobFactsRow
from app.search.embed import get_embedder, model_keys
from app.search.text import VARIANTS, build_job_search_text, facts_from_row


def process_jobs(s: Session, job_ids: Sequence[int], models: Sequence[str] | None = None, batch: int = 128,
                 **_: object) -> dict[str, int]:
    """Embed jobs in batches; upsert job_embeddings; update search_text (drives the FTS column)."""
    models = list(models or model_keys())
    n = 0
    ids = list(job_ids)
    for i in range(0, len(ids), batch):
        chunk = ids[i:i + batch]
        rows = s.execute(
            select(Job, JobFactsRow.facts).outerjoin(JobFactsRow, JobFactsRow.job_id == Job.id).where(Job.id.in_(chunk))
        ).all()
        texts: dict[str, list[str]] = {v: [] for v in VARIANTS}
        for job, facts in rows:
            f = facts_from_row(facts)
            for v in VARIANTS:
                texts[v].append(build_job_search_text(job.title, job.company or None, job.description_text, f, v))
            job.search_text = texts["structured"][-1]
        for m in models:
            emb = get_embedder(m)
            for v in VARIANTS:
                vecs = emb.embed_docs(texts[v])
                values = [{"job_id": job.id, "model_name": m, "text_variant": v, "embedding": vec}
                          for (job, _), vec in zip(rows, vecs, strict=True)]
                if values:
                    stmt = insert(JobEmbedding).values(values)
                    s.execute(stmt.on_conflict_do_update(
                        index_elements=["job_id", "model_name", "text_variant"],
                        set_={"embedding": stmt.excluded.embedding}))
        s.commit()
        n += len(rows)
    return {"embedded_jobs": n, "models": len(models), "variants": len(VARIANTS)}
