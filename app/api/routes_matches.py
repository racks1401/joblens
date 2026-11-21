"""GET /matches/{profile_id}?limit=20&show_ineligible=false and the explain route (added in P6)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.routes_profiles import load_profile
from app.config import get_settings
from app.db import get_session
from app.search.service import find_by_name, match_page

router = APIRouter(tags=["matches"])


@router.get("/matches/{profile_id}")
def matches_route(
    profile_id: uuid.UUID,
    limit: int = Query(20, ge=1, le=50),
    show_ineligible: bool = False,
    include_out_of_scope: bool = False,
    include_expired: bool = False,
    s: Session = Depends(get_session),
) -> dict[str, Any]:
    """Ranked matches with final score, eligibility (rule lines + evidence) and trust. With show_ineligible,
    INELIGIBLE jobs come after ELIGIBLE and CHECK ones. `total` counts the matches before `limit`; all of them come
    from the top `candidates` retrieval results for the profile."""
    _, profile = load_profile(s, profile_id)
    items, total = match_page(s, profile, limit=limit, show_ineligible=show_ineligible,
                              include_out_of_scope=include_out_of_scope, include_expired=include_expired)
    return {"profile_id": str(profile_id), "results": [i.model_dump(mode="json") for i in items], "total": total,
            "candidates": get_settings().cfg.search.candidates}


@router.get("/matches/{profile_id}/find")
def find_route(
    profile_id: uuid.UUID,
    q: str = Query(..., min_length=2, max_length=100),
    limit: int = Query(20, ge=1, le=50),
    s: Session = Depends(get_session),
) -> dict[str, Any]:
    """Jobs whose company or title contains q, with eligibility for this profile, any verdict, no candidate cap."""
    _, profile = load_profile(s, profile_id)
    hits, total = find_by_name(s, q, profile, limit)
    return {"profile_id": str(profile_id), "query": q, "results": [h.model_dump(mode="json") for h in hits],
            "total": total}
