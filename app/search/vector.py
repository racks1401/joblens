"""Vector retrieval: pgvector cosine distance over job_embeddings for one model + text variant."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Job, JobEmbedding


def vector_search(s: Session, qvec: Sequence[float], model: str, variant: str, limit: int = 50,
                  include_out_of_scope: bool = False) -> list[tuple[int, float]]:
    """Active jobs nearest to qvec: [(job_id, similarity = 1 - cosine distance)]."""
    dist = JobEmbedding.embedding.cosine_distance(list(qvec)).label("dist")
    q = (
        select(JobEmbedding.job_id, dist)
        .join(Job, Job.id == JobEmbedding.job_id)
        .where(Job.is_active.is_(True), JobEmbedding.model_name == model, JobEmbedding.text_variant == variant)
        .where(Job.in_scope.is_(True) if not include_out_of_scope else Job.id.is_not(None))
        .order_by(dist, JobEmbedding.job_id)
        .limit(limit)
    )
    return [(int(r.job_id), 1.0 - float(r.dist)) for r in s.execute(q)]
