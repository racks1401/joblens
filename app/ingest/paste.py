"""User-pasted job text -> NormalizedJob (source='paste')."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime

from app.ingest.normalize import clean_whitespace, html_to_text
from app.schemas import NormalizedJob


def guess_title(text: str) -> str:
    """First meaningful line (or an explicit 'Title:'/'Role:' line), max 120 chars."""
    m = re.search(r"^(?:job\s*title|title|role|position)\s*[:\-]\s*(.+)$", text, re.I | re.M)
    if m:
        return m.group(1).strip()[:120]
    for ln in text.splitlines():
        ln = ln.strip(" -*#\t")
        if len(ln) >= 3:
            return ln[:120]
    return "Untitled job"


def from_paste(text: str, url: str | None = None, company: str | None = None,
               now: datetime | None = None) -> NormalizedJob:
    """Build a NormalizedJob from pasted text. source_job_id = sha256(url or text)."""
    body = html_to_text(text) if re.search(r"<(p|div|li|br)\b", text, re.I) else clean_whitespace(text)[:20000]
    key = (url or "").strip() or body
    return NormalizedJob(
        source="paste",
        source_job_id=hashlib.sha256(key.encode()).hexdigest(),
        company=(company or "").strip(),
        title=guess_title(body),
        location_raw=None,
        url=(url or None),
        description_text=body,
        posted_at=None,
        fetched_at=now or datetime.now(UTC),
    )
