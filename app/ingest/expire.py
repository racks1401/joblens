"""Expired jobs: a job whose extracted application deadline is before today (India date) is marked expired.

Expired rows are kept. They are left out of the default /matches, /search and the agent's search_jobs; get_job
still returns them with "expired": true. Re-running recomputes the flag, so a corrected or later deadline
clears it.

python -m app.ingest.expire            # dry run: list the jobs that would change
python -m app.ingest.expire --apply    # write the flag
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Job, JobFactsRow
from app.schemas import JobFacts

IST = ZoneInfo("Asia/Kolkata")


def today_ist() -> date:
    return datetime.now(IST).date()


def deadline_of(facts: dict[str, Any] | None) -> date | None:
    if not facts:
        return None
    return JobFacts.model_validate(facts).apply_deadline.value


def is_expired(deadline: date | None, today: date) -> bool:
    """Past deadline only: a job whose deadline is today is still open."""
    return deadline is not None and deadline < today


def plan(s: Session, today: date | None = None, job_ids: Sequence[int] | None = None) -> list[dict[str, Any]]:
    """Jobs whose expired flag would change: [{job_id, title, company, deadline, in_scope, expired_now, expired_new}]."""
    today = today or today_ist()
    q = select(Job, JobFactsRow.facts).outerjoin(JobFactsRow, JobFactsRow.job_id == Job.id)
    if job_ids is not None:
        q = q.where(Job.id.in_(list(job_ids)))
    out = []
    for job, facts in s.execute(q).all():
        dl = deadline_of(facts)
        new = is_expired(dl, today)
        if new != job.expired:
            out.append({"job_id": job.id, "title": job.title, "company": job.company, "deadline": dl,
                        "in_scope": job.in_scope, "is_active": job.is_active, "expired_now": job.expired,
                        "expired_new": new})
    return out


def apply(s: Session, changes: list[dict[str, Any]]) -> int:
    for c in changes:
        job = s.get(Job, c["job_id"])
        if job is not None:
            job.expired = c["expired_new"]
    s.commit()
    return len(changes)


def process_jobs(s: Session, job_ids: Sequence[int], **_: object) -> dict[str, int]:
    """Pipeline step (after facts): recompute the expired flag for these jobs."""
    ch = plan(s, job_ids=job_ids)
    apply(s, ch)
    return {"expired": sum(1 for c in ch if c["expired_new"]), "unexpired": sum(1 for c in ch if not c["expired_new"])}


def main() -> None:
    from app.db import session_factory

    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--today", type=date.fromisoformat, default=None, help="override today's date (YYYY-MM-DD)")
    args = ap.parse_args()
    with session_factory()() as s:
        today = args.today or today_ist()
        ch = plan(s, today)
        print(f"today (IST) {today}: {len(ch)} job(s) change")
        for c in ch:
            print(f"  #{c['job_id']} {c['title']} | {c['company'] or '-'} | deadline {c['deadline']} | "
                  f"in_scope {c['in_scope']} active {c['is_active']} | expired {c['expired_now']} -> {c['expired_new']}")
        if args.apply:
            print("applied", apply(s, ch))
        else:
            print("dry run: nothing written (use --apply)")


if __name__ == "__main__":
    main()
