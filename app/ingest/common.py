"""Shared fetch result type and date parsing for the ATS clients."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.schemas import NormalizedJob


@dataclass
class FetchResult:
    """Outcome of fetching one company board. ok=False means the token check failed."""

    ok: bool
    status: int
    jobs: list[NormalizedJob] = field(default_factory=list)
    raw_count: int = 0


def parse_dt(v: Any) -> datetime | None:
    """Parse ISO strings or epoch milliseconds into aware datetimes."""
    if v is None or v == "":
        return None
    if isinstance(v, int | float):
        return datetime.fromtimestamp(v / 1000, tz=UTC)
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def join_locations(*locs: str | None, remote: bool = False) -> str | None:
    """Unique, ordered ' / '-joined locations; adds 'Remote' when the board flags it."""
    out: list[str] = []
    for loc in locs:
        if loc and loc.strip() and loc.strip().lower() not in {x.lower() for x in out}:
            out.append(loc.strip())
    if remote and not any("remote" in x.lower() for x in out):
        out.append("Remote")
    return " / ".join(out) or None
