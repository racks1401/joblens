"""Lever Postings API: GET api.lever.co/v0/postings/{company}?mode=json."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.ingest.common import FetchResult, join_locations, parse_dt
from app.ingest.http import PoliteClient
from app.ingest.normalize import clean_whitespace, html_to_text
from app.schemas import CompanyCfg, NormalizedJob

URL = "https://api.lever.co/v0/postings/{token}"


def location_of(raw: dict[str, Any]) -> str | None:
    """categories.location + allLocations; 'Remote' if workplaceType is remote."""
    cat = raw.get("categories") or {}
    all_locs = cat.get("allLocations") or []
    return join_locations(cat.get("location"), *all_locs, remote=raw.get("workplaceType") == "remote")


def description_of(raw: dict[str, Any]) -> str:
    """Description + each list section (with its heading) + additional info."""
    parts = [html_to_text(raw.get("description")) or (raw.get("descriptionPlain") or "")]
    for lst in raw.get("lists") or []:
        heading = (lst.get("text") or "").strip()
        body = html_to_text("<ul>" + (lst.get("content") or "") + "</ul>")
        parts.append(f"{heading}\n{body}" if heading else body)
    parts.append(html_to_text(raw.get("additional")) or (raw.get("additionalPlain") or ""))
    return clean_whitespace("\n\n".join(p for p in parts if p))[:20000]


def map_job(raw: dict[str, Any], company: str, fetched_at: datetime) -> NormalizedJob:
    """Map one Lever posting to NormalizedJob."""
    return NormalizedJob(
        source="lever",
        source_job_id=str(raw["id"]),
        company=company,
        title=str(raw.get("text") or "").strip(),
        location_raw=location_of(raw),
        url=raw.get("hostedUrl"),
        description_text=description_of(raw),
        posted_at=parse_dt(raw.get("createdAt")),
        fetched_at=fetched_at,
    )


def fetch(client: PoliteClient, c: CompanyCfg, fetched_at: datetime) -> FetchResult:
    """Fetch and map all postings. Non-200 -> ok=False."""
    status, data = client.get_json(URL.format(token=c.token), {"mode": "json"})
    if status != 200 or not isinstance(data, list):
        return FetchResult(False, status)
    return FetchResult(True, status, [map_job(r, c.name, fetched_at) for r in data], len(data))
