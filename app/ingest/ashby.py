"""Ashby Job Posting API: GET api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.ingest.common import FetchResult, join_locations, parse_dt
from app.ingest.http import PoliteClient
from app.ingest.normalize import clean_whitespace, html_to_text
from app.schemas import CompanyCfg, NormalizedJob

URL = "https://api.ashbyhq.com/posting-api/job-board/{token}"


def location_of(raw: dict[str, Any]) -> str | None:
    """Primary + secondary locations; 'Remote' if isRemote."""
    sec = [s.get("location") for s in raw.get("secondaryLocations") or [] if isinstance(s, dict)]
    return join_locations(raw.get("location"), *sec, remote=bool(raw.get("isRemote")))


def map_job(raw: dict[str, Any], company: str, fetched_at: datetime) -> NormalizedJob:
    """Map one Ashby job to NormalizedJob. Published compensation is appended to the text."""
    desc = html_to_text(raw.get("descriptionHtml")) or clean_whitespace(raw.get("descriptionPlain") or "")
    comp = (raw.get("compensation") or {}).get("compensationTierSummary")
    if comp and comp not in desc:
        desc = f"{desc}\n\nCompensation: {comp}"
    return NormalizedJob(
        source="ashby",
        source_job_id=str(raw["id"]),
        company=company,
        title=str(raw.get("title") or "").strip(),
        location_raw=location_of(raw),
        url=raw.get("jobUrl"),
        description_text=desc[:20000],
        posted_at=parse_dt(raw.get("publishedAt")),
        fetched_at=fetched_at,
    )


def fetch(client: PoliteClient, c: CompanyCfg, fetched_at: datetime) -> FetchResult:
    """Fetch and map listed jobs. Non-200 -> ok=False."""
    status, data = client.get_json(URL.format(token=c.token), {"includeCompensation": "true"})
    if status != 200 or not isinstance(data, dict) or "jobs" not in data:
        return FetchResult(False, status)
    raws = [r for r in data["jobs"] if r.get("isListed", True)]
    return FetchResult(True, status, [map_job(r, c.name, fetched_at) for r in raws], len(raws))
