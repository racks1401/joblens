"""GET /search?q=&mode=keyword|vector|hybrid&model=&variant=&limit= (+ optional filters)."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.errors import APIError
from app.api.routes_profiles import load_profile
from app.config import get_settings
from app.db import get_session
from app.search.service import Filters, search

router = APIRouter(tags=["search"])


@router.get("/search")
def search_route(
    q: str = Query(..., min_length=1, max_length=500),
    mode: Literal["keyword", "vector", "hybrid"] = "hybrid",
    model: str | None = None,
    variant: Literal["raw", "structured"] | None = None,
    limit: int = Query(20, ge=1, le=50),
    employment_type: str | None = None,
    work_mode: str | None = None,
    location: str | None = None,
    min_monthly_pay_inr: int | None = None,
    fresher_only: bool = False,
    profile_id: uuid.UUID | None = None,
    include_out_of_scope: bool = False,
    s: Session = Depends(get_session),
) -> dict[str, Any]:
    if model and model not in get_settings().cfg.embeddings.models:
        raise APIError(400, "unknown_model", f"model must be one of {list(get_settings().cfg.embeddings.models)}")
    profile = load_profile(s, profile_id)[1] if profile_id else None
    flt = Filters(employment_type=employment_type, work_mode=work_mode, location=location,
                  min_monthly_pay_inr=min_monthly_pay_inr, fresher_only=fresher_only)
    out = search(s, q, mode, model, variant, limit, flt if flt.active() else None, profile, include_out_of_scope)
    out["results"] = [r.model_dump(mode="json") for r in out["results"]]
    return out
