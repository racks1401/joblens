"""Post-ingestion processing for new/changed jobs: extraction -> search text + embeddings -> trust.

Steps are looked up lazily so the pipeline works as phases are added; each step takes
(session, job_ids) and commits its own work.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable, Sequence

from sqlalchemy.orm import Session

from app.logs import log_event

log = logging.getLogger("joblens.pipeline")

STEPS: list[tuple[str, str]] = [
    ("facts", "app.extract.facts:process_jobs"),
    ("search", "app.search.index:process_jobs"),
    ("trust", "app.trust.score:process_jobs"),
]


def _load(path: str) -> Callable[..., object] | None:
    mod, attr = path.split(":")
    try:
        return getattr(importlib.import_module(mod), attr)  # type: ignore[no-any-return]
    except ModuleNotFoundError as e:
        if e.name and mod.startswith(e.name):
            return None
        raise


def process_jobs(s: Session, job_ids: Sequence[int], steps: Sequence[str] | None = None, **kw: object) -> dict[str, object]:
    """Run each available step over job_ids. Returns {step: result or 'unavailable'}."""
    out: dict[str, object] = {}
    if not job_ids:
        return out
    for name, path in STEPS:
        if steps is not None and name not in steps:
            continue
        fn = _load(path)
        if fn is None:
            out[name] = "unavailable"
            continue
        out[name] = fn(s, list(job_ids), **kw)
        log_event(log, "pipeline_step", step=name, jobs=len(job_ids), result=out[name])
    return out


def main() -> None:
    """python -m app.pipeline [--steps facts,search,trust] [--all]

    Default: process active jobs missing facts. --all re-processes every active job (e.g. after
    adding an LLM key or changing the prompt version).
    """
    import argparse

    from sqlalchemy import case, or_, select

    from app.config import get_settings
    from app.db import session_factory
    from app.llm.client import DailyLimitReached, get_llm
    from app.logs import setup_logging
    from app.models import Job, JobFactsRow

    setup_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", default="facts,search,trust")
    ap.add_argument("--all", action="store_true", help="re-process every active job")
    ap.add_argument("--stale", action="store_true",
                    help="only jobs whose facts came from a different model or prompt version (resumable)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--chunk", type=int, default=25)
    ap.add_argument("--include-out-of-scope", action="store_true", help="also process jobs that are senior or not open to India")
    args = ap.parse_args()
    st = get_settings()
    model = get_llm("extract").model
    # Fresher-relevant titles first, then India locations, so a partial run covers the useful jobs.
    priority = case((Job.title.op("~*")(r"intern|graduate|fresher|entry|junior|trainee|associate|new grad"), 0),
                    else_=1)
    india = case((Job.location_raw.op("~*")("|".join(st.cfg.ingest.india_keywords)), 0), else_=1)
    with session_factory()() as s:
        q = (select(Job.id).where(Job.is_active.is_(True))
             .where(Job.in_scope.is_(True) if not args.include_out_of_scope else Job.id.is_not(None))
             .outerjoin(JobFactsRow, JobFactsRow.job_id == Job.id)
             .order_by(priority, india, Job.id))
        if args.stale:
            q = q.where(or_(JobFactsRow.job_id.is_(None), JobFactsRow.model != model,
                            JobFactsRow.prompt_version != case(
                                (Job.source == "paste", st.cfg.prompts.facts_paste or st.cfg.prompts.facts),
                                else_=st.cfg.prompts.facts),
                            JobFactsRow.valid.is_(False)))
        elif not args.all:
            q = q.where(JobFactsRow.job_id.is_(None))
        ids = list(s.execute(q).scalars())[: args.limit or None]
        print(f"model={model} jobs={len(ids)}", flush=True)
        totals: dict[str, int] = {}
        done = 0
        try:
            for i in range(0, len(ids), args.chunk):
                out = process_jobs(s, ids[i:i + args.chunk], steps=args.steps.split(","))
                for v in out.values():
                    if isinstance(v, dict):
                        for k, n in v.items():
                            if isinstance(n, int):
                                totals[k] = totals.get(k, 0) + n
                done += len(ids[i:i + args.chunk])
                print(f"progress {done}/{len(ids)} {totals}", flush=True)
        except DailyLimitReached as e:
            print(f"STOPPED: provider daily limit reached after {done} jobs ({e}). Re-run with --stale to resume.")
        print("final", totals, f"done={done}/{len(ids)}")


if __name__ == "__main__":
    main()
