"""Upsert NormalizedJobs into `jobs`: first/last seen, deactivation, content-hash changes, reposts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.ingest.normalize import content_hash, norm_key
from app.models import Job, JobFactsRow
from app.schemas import NormalizedJob


@dataclass
class UpsertStats:
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    deactivated: int = 0
    to_process: list[int] = field(default_factory=list)  # job ids needing extraction/embedding/trust


def _repost_count(s: Session, nj: NormalizedJob, now: datetime, window_days: int) -> int:
    """For a new job: 1 + max repost_count of same company+title first seen within the window, else 0."""
    q = select(func.max(Job.repost_count), func.count(Job.id)).where(
        func.lower(func.regexp_replace(Job.company, r"\s+", " ", "g")) == norm_key(nj.company),
        func.lower(func.regexp_replace(Job.title, r"\s+", " ", "g")) == norm_key(nj.title),
        Job.first_seen_at >= now - timedelta(days=window_days),
    )
    mx, n = s.execute(q).one()
    return int(mx or 0) + 1 if n else 0


def upsert_job(s: Session, nj: NormalizedJob, now: datetime, window_days: int = 90) -> tuple[Job, str]:
    """Insert or update one job. Returns (row, 'new' | 'changed' | 'unchanged'). Does not commit."""
    h = content_hash(nj.company, nj.title, nj.description_text)
    row = s.execute(select(Job).where(Job.source == nj.source, Job.source_job_id == nj.source_job_id)).scalar_one_or_none()
    if row is None:
        row = Job(
            source=nj.source, source_job_id=nj.source_job_id, company=nj.company, title=nj.title,
            location_raw=nj.location_raw, url=nj.url, description_text=nj.description_text, content_hash=h,
            posted_at=nj.posted_at, first_seen_at=now, last_seen_at=now, is_active=True,
            repost_count=_repost_count(s, nj, now, window_days),
        )
        s.add(row)
        s.flush()
        return row, "new"
    row.last_seen_at, row.is_active = now, True
    row.location_raw, row.url = nj.location_raw, nj.url
    row.posted_at = nj.posted_at or row.posted_at
    if row.content_hash != h:
        row.company, row.title, row.description_text, row.content_hash = nj.company, nj.title, nj.description_text, h
        # Facts extracted from the old text no longer have evidence in the new text: drop them so the
        # job is re-extracted (pipeline step, or `python -m app.pipeline --stale` later).
        s.execute(delete(JobFactsRow).where(JobFactsRow.job_id == row.id))
        s.flush()
        return row, "changed"
    s.flush()
    return row, "unchanged"


def upsert_company(s: Session, source: str, company: str, jobs: list[NormalizedJob], now: datetime,
                   window_days: int = 90, on_board: set[str] | None = None) -> UpsertStats:
    """Upsert a company's fetched jobs and deactivate its jobs not seen in this run. Does not commit."""
    st = UpsertStats()
    for nj in jobs:
        row, kind = upsert_job(s, nj, now, window_days)
        setattr(st, kind, getattr(st, kind) + 1)
        if kind != "unchanged":
            st.to_process.append(row.id)
    # Deactivate only jobs that are no longer on the board. A job still on the board but not selected
    # this run (filters/cap) keeps its state. on_board=None means "everything fetched was passed in".
    q = update(Job).where(Job.source == source, Job.company == company, Job.is_active.is_(True),
                          Job.last_seen_at < now)
    if on_board is not None:
        q = q.where(Job.source_job_id.not_in(list(on_board)) if on_board else Job.id.is_not(None))
    res = s.execute(q.values(is_active=False))
    st.deactivated = res.rowcount or 0  # type: ignore[attr-defined]
    return st
