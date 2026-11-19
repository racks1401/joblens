"""User-pasted job text -> NormalizedJob (source='paste')."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime

from app.ingest.normalize import clean_whitespace, html_to_text
from app.schemas import NormalizedJob

# "Key: value" lines that describe the job but are never its title (Company: X, Pay: ..., Location: ...).
_HEADER = re.compile(r"^(?:company|organi[sz]ation|employer|location|locations|pay|salary|stipend|ctc|package|"
                     r"work|work mode|mode|type|job type|employment type|duration|experience|eligibility|"
                     r"website|apply|deadline|last date|email|contact)\s*:", re.I)


def guess_title(text: str) -> str:
    """Provisional title: an explicit 'Title:'/'Role:' line, else the first line that is not a
    'Key: value' header, a section heading (ALL CAPS or ending with ':') or a bullet. The extracted
    role (JobFacts.title) replaces it after extraction."""
    m = re.search(r"^(?:job\s*title|title|role|position)\s*[:\-]\s*(.+)$", text, re.I | re.M)
    if m:
        return m.group(1).strip()[:120]
    for raw in text.splitlines():
        ln = raw.strip().lstrip("#").strip()
        if (len(ln) < 3 or _HEADER.match(ln) or ln.endswith(":") or ln[:1] in "-*•"
                or (ln.isupper() and len(ln.split()) <= 6)):
            continue
        return ln[:120]
    return "Untitled job"


def company_from_text(text: str) -> str | None:
    """Company from a 'Company: X' line, if present."""
    m = re.search(r"^(?:company|organi[sz]ation|employer)\s*:\s*(.+)$", text, re.I | re.M)
    return m.group(1).strip()[:200] if m else None


def from_paste(text: str, url: str | None = None, company: str | None = None,
               now: datetime | None = None) -> NormalizedJob:
    """Build a NormalizedJob from pasted text. source_job_id = sha256(url or text)."""
    body = html_to_text(text) if re.search(r"<(p|div|li|br)\b", text, re.I) else clean_whitespace(text)[:20000]
    key = (url or "").strip() or body
    return NormalizedJob(
        source="paste",
        source_job_id=hashlib.sha256(key.encode()).hexdigest(),
        company=(company or company_from_text(body) or "").strip(),
        title=guess_title(body),
        location_raw=None,
        url=(url or None),
        description_text=body,
        posted_at=None,
        fetched_at=now or datetime.now(UTC),
    )
