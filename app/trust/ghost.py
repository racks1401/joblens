"""Ghost-job signals (spec 13): listing age and reposts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.schemas import TrustReason


def listing_age(posted_at: datetime | None, first_seen_at: datetime | None, now: datetime, deltas: dict[str, int]
                ) -> TrustReason | None:
    """Age = now - (posted_at or first_seen_at). Over 120 days: age_120; over 60 days: age_60 (not both)."""
    start = posted_at or first_seen_at
    if start is None:
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    days = (now - start).days
    if days > 120:
        return TrustReason(code="age_120", message=f"Posted {days} days ago", delta=deltas["age_120"])
    if days > 60:
        return TrustReason(code="age_60", message=f"Posted {days} days ago", delta=deltas["age_60"])
    return None


def reposted(repost_count: int, repost_min: int, delta: int) -> TrustReason | None:
    if repost_count >= repost_min:
        return TrustReason(code="repost", message=f"Reposted {repost_count} times in 90 days", delta=delta)
    return None


def run_ghost(job: Any, cfg: Any, now: datetime | None = None) -> list[TrustReason]:
    now = now or datetime.now(UTC)
    out = [listing_age(job.posted_at, job.first_seen_at, now, cfg.deltas),
           reposted(job.repost_count or 0, cfg.repost_min, cfg.deltas["repost"])]
    return [r for r in out if r]
