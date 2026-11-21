"""GET /health: DB, pgvector, LLM provider, embedding and classifier status."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter

from app.config import ROOT, get_settings
from app.db import db_status

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, Any]:
    """Green ('ok') when the DB and pgvector are reachable; extra components are informational."""
    s = get_settings()
    db = db_status()
    clf = Path(s.cfg.trust.classifier_path)
    clf = clf if clf.is_absolute() else ROOT / clf
    emb_cache = Path(s.env.fastembed_cache)
    status = "ok" if db["ok"] and db["pgvector"] else "degraded"
    return {
        "status": status,
        "db": db,
        "llm": {"provider": s.llm_provider, "models": s.cfg.models.model_dump()},
        "embeddings": {
            "backend": s.env.embedder,
            "default_model": s.cfg.embeddings.default_model,
            "cache_present": emb_cache.exists() and any(emb_cache.iterdir()),
        },
        "scam_classifier": {"present": clf.exists()},
        "public_demo": s.env.public_demo,
    }
