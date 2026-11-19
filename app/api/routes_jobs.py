"""Jobs: POST /jobs/paste, GET /jobs/{id}, POST /admin/ingest."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.api.errors import APIError
from app.db import get_session
from app.ingest.paste import from_paste
from app.ingest.store import upsert_job
from app.models import Job, JobFactsRow, TrustScore
from app.schemas import JobOut, PasteJobIn, TrustResult

router = APIRouter(tags=["jobs"])


def job_out(s: Session, job: Job, include_text: bool = True) -> JobOut:
    """Job + stored JobFacts + trust (whatever exists)."""
    facts = s.get(JobFactsRow, job.id)
    trust = s.get(TrustScore, job.id)
    return JobOut(
        id=job.id, source=job.source, company=job.company, title=job.title, location_raw=job.location_raw,
        url=job.url, posted_at=job.posted_at, first_seen_at=job.first_seen_at, last_seen_at=job.last_seen_at,
        is_active=job.is_active, repost_count=job.repost_count,
        description_text=job.description_text if include_text else None,
        facts=facts.facts if facts else None, facts_valid=facts.valid if facts else None,
        trust=TrustResult(score=trust.score, level=trust.level, reasons=trust.reasons, scam_prob=trust.scam_prob)  # type: ignore[arg-type]
        if trust else None,
        flags=[job.title.lower()] if job.title in ("Title unknown", "Needs human title") else [],
    )


@router.post("/jobs/paste", response_model=JobOut)
def paste_job(body: PasteJobIn, s: Session = Depends(get_session)) -> JobOut:
    """Add a pasted job and run it through the same pipeline as ingested jobs."""
    from app.ingest.split import split_post
    from app.llm.client import get_llm
    from app.pipeline import process_jobs

    nj = from_paste(body.text, body.url, body.company)
    # A post listing several roles becomes one pasted job per role (original lines, shared header/footer copied).
    pieces = split_post(nj.description_text, get_llm("extract"))
    njs = [nj]
    if pieces:
        njs = []
        for p in pieces:
            pj = from_paste(p.text, body.url, nj.company or body.company)
            njs.append(pj.model_copy(update={"title": p.title,
                                             "source_job_id": f"role{p.index}-{nj.source_job_id}"}))
    rows = []
    for one in njs:
        row, kind = upsert_job(s, one, datetime.now(UTC))
        s.commit()
        if kind != "unchanged" or s.get(JobFactsRow, row.id) is None:
            process_jobs(s, [row.id])
        s.refresh(row)
        rows.append(row)
    out = job_out(s, rows[0])
    out.related_job_ids = [r.id for r in rows[1:]]
    return out


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: int, s: Session = Depends(get_session)) -> JobOut:
    job = s.get(Job, job_id)
    if job is None:
        raise APIError(404, "job_not_found", f"No job with id {job_id}")
    return job_out(s, job)


def _ingest_task() -> None:
    from app.config import get_settings
    from app.db import session_factory
    from app.ingest.run import load_companies, make_client, run_ingest

    st = get_settings()
    client = make_client(st)
    with session_factory()() as s:
        run_ingest(s, load_companies(st.env.companies_file), client, settings=st)
    client.close()


@router.post("/admin/ingest", status_code=202, dependencies=[Depends(require_admin)])
def admin_ingest(bg: BackgroundTasks) -> dict[str, Any]:
    """Start ingestion in the background (API key required)."""
    bg.add_task(_ingest_task)
    return {"status": "started"}
