"""Greenhouse Job Board API: GET boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.ingest.common import FetchResult, join_locations, parse_dt
from app.ingest.http import PoliteClient
from app.ingest.normalize import html_to_text
from app.schemas import CompanyCfg, NormalizedJob

URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


def location_of(raw: dict[str, Any]) -> str | None:
    """Location string from a Greenhouse job (location.name plus office names)."""
    offices = [o.get("name") for o in raw.get("offices") or [] if isinstance(o, dict)]
    return join_locations((raw.get("location") or {}).get("name"), *offices)


def map_job(raw: dict[str, Any], company: str, fetched_at: datetime) -> NormalizedJob:
    """Map one Greenhouse job to NormalizedJob."""
    return NormalizedJob(
        source="greenhouse",
        source_job_id=str(raw["id"]),
        company=company,
        title=str(raw.get("title") or "").strip(),
        location_raw=location_of(raw),
        url=raw.get("absolute_url"),
        description_text=html_to_text(raw.get("content")),
        posted_at=parse_dt(raw.get("first_published") or raw.get("updated_at")),
        fetched_at=fetched_at,
    )


def fetch(client: PoliteClient, c: CompanyCfg, fetched_at: datetime, content: bool = True) -> FetchResult:
    """Fetch and map all jobs for a board token. Non-200 -> ok=False."""
    status, data = client.get_json(URL.format(token=c.token), {"content": "true"} if content else None)
    if status != 200 or not isinstance(data, dict) or "jobs" not in data:
        return FetchResult(False, status)
    raws = data["jobs"]
    return FetchResult(True, status, [map_job(r, c.name, fetched_at) for r in raws], len(raws))
