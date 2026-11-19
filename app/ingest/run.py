"""Ingestion CLI: python -m app.ingest.run [--company NAME] [--no-process].

For each company in config/companies.yaml: fetch the board (a non-200 is logged as a token-check
warning and the company is skipped without deactivating anything), keep India/remote jobs, upsert,
deactivate unseen jobs, then run the processing pipeline on new/changed jobs.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.ingest import ashby, greenhouse, lever
from app.ingest.common import FetchResult
from app.ingest.http import PoliteClient
from app.ingest.normalize import location_matches
from app.ingest.role_family import TECH_FAMILIES, role_for_job
from app.ingest.seniority import classify
from app.ingest.store import upsert_company
from app.logs import log_event, setup_logging
from app.schemas import CompanyCfg, NormalizedJob

log = logging.getLogger("joblens.ingest")

FETCHERS: dict[str, Callable[[PoliteClient, CompanyCfg, datetime], FetchResult]] = {
    "greenhouse": greenhouse.fetch,
    "lever": lever.fetch,
    "ashby": ashby.fetch,
}


def load_companies(path: str | Path) -> list[CompanyCfg]:
    """Parse companies.yaml."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    return [CompanyCfg.model_validate(x) for x in data]


def make_client(s: Settings, **kw: Any) -> PoliteClient:
    i = s.cfg.ingest
    return PoliteClient(i.user_agent, rps=i.requests_per_second, timeout_s=i.timeout_s, retries=i.retries, **kw)


def in_target(j: NormalizedJob) -> bool:
    """New jobs worth storing: fresher-relevant and a tech role family (engineering, data_ml, product)."""
    return classify(j.title, j.description_text)[0] and role_for_job(f"{j.source}:{j.source_job_id}", j.title)[0] in TECH_FAMILIES


def select_jobs(jobs: list[NormalizedJob], s: Settings, known_ids: set[str] | None = None) -> list[NormalizedJob]:
    """Keep India/remote jobs with text, then apply the per-company cap to NEW jobs only.

    Jobs already stored for this company (known_ids) and still on the board are always kept, so the
    cap never pushes a live job out of the pool. New jobs fill the remaining slots, India first, then newest.
    """
    cfg = s.cfg.ingest
    known_ids = known_ids or set()
    kept = [j for j in jobs if j.title and j.description_text and location_matches(j.location_raw, cfg)]
    if not cfg.max_jobs_per_company:
        return [j for j in kept if j.source_job_id in known_ids or in_target(j)]
    known = [j for j in kept if j.source_job_id in known_ids]
    # New jobs: only fresher-relevant ones are added (senior roles already stored are kept, marked).
    new = [j for j in kept if j.source_job_id not in known_ids and in_target(j)]

    def india(j: NormalizedJob) -> bool:
        loc = (j.location_raw or "").lower()
        return any(k in loc for k in cfg.india_keywords)

    oldest = datetime.min.replace(tzinfo=UTC)
    new.sort(key=lambda j: (not india(j), -(j.posted_at or oldest).timestamp(), j.source_job_id))
    # The cap counts fresher-relevant jobs only: stored senior rows are refreshed but take no slot.
    known_fresher = sum(1 for j in known if in_target(j))
    return known + new[: max(0, cfg.max_jobs_per_company - known_fresher)]


def active_ids(session: Session, source: str, company: str) -> set[str]:
    """source_job_ids of this company's active stored jobs."""
    from sqlalchemy import select

    from app.models import Job

    q = select(Job.source_job_id).where(Job.source == source, Job.company == company, Job.is_active.is_(True))
    return set(session.execute(q).scalars())


def run_ingest(session: Session, companies: list[CompanyCfg], client: PoliteClient, *,
               settings: Settings | None = None, process: bool = True, now: datetime | None = None) -> dict[str, Any]:
    """Ingest all companies. Returns a summary dict."""
    s = settings or get_settings()
    now = now or datetime.now(UTC)
    summary: dict[str, Any] = {"companies_ok": 0, "companies_failed": [], "new": 0, "changed": 0,
                               "unchanged": 0, "deactivated": 0, "filtered_out": 0, "processed": {}}
    to_process: list[int] = []
    for c in companies:
        res = FETCHERS[c.source](client, c, now)
        if not res.ok:
            log_event(log, "token_check_failed", level=logging.WARNING, company=c.name, source=c.source,
                      token=c.token, status=res.status)
            summary["companies_failed"].append({"name": c.name, "status": res.status})
            continue
        kept = select_jobs(res.jobs, s, active_ids(session, c.source, c.name))
        summary["filtered_out"] += len(res.jobs) - len(kept)
        st = upsert_company(session, c.source, c.name, kept, now, s.cfg.ingest.repost_window_days,
                            on_board={j.source_job_id for j in res.jobs})
        session.commit()
        summary["companies_ok"] += 1
        for k in ("new", "changed", "unchanged", "deactivated"):
            summary[k] += getattr(st, k)
        to_process += st.to_process
        log_event(log, "company_ingested", company=c.name, fetched=len(res.jobs), kept=len(kept),
                  new=st.new, changed=st.changed, deactivated=st.deactivated)
    if process and to_process:
        from app.pipeline import process_jobs

        summary["processed"] = process_jobs(session, to_process)
    summary["to_process"] = len(to_process)
    return summary


def main() -> None:
    setup_logging()
    ap = argparse.ArgumentParser(description="Ingest jobs from public ATS boards")
    ap.add_argument("--company", action="append", help="only these company names")
    ap.add_argument("--no-process", action="store_true", help="skip extraction/embedding/trust")
    args = ap.parse_args()
    s = get_settings()
    companies = load_companies(s.env.companies_file)
    if args.company:
        wanted = {n.lower() for n in args.company}
        companies = [c for c in companies if c.name.lower() in wanted]
    from app.db import session_factory

    client = make_client(s)
    with session_factory()() as session:
        summary = run_ingest(session, companies, client, settings=s, process=not args.no_process)
    client.close()
    log_event(log, "ingest_done", **summary)
    print(summary)


if __name__ == "__main__":
    main()
