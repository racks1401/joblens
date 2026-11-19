"""GET /matches/{profile_id}?limit=20&show_ineligible=false and the explain route (added in P6)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.routes_profiles import load_profile
from app.db import get_session
from app.search.service import match

router = APIRouter(tags=["matches"])


@router.get("/matches/{profile_id}")
def matches_route(
    profile_id: uuid.UUID,
    limit: int = Query(20, ge=1, le=50),
    show_ineligible: bool = False,
    include_senior: bool = False,
    s: Session = Depends(get_session),
) -> dict[str, Any]:
    """Ranked matches with final score, eligibility (rule lines + evidence) and trust."""
    _, profile = load_profile(s, profile_id)
    items = match(s, profile, limit=limit, show_ineligible=show_ineligible, include_senior=include_senior)
    return {"profile_id": str(profile_id), "results": [i.model_dump(mode="json") for i in items]}
