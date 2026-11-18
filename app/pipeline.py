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

    from sqlalchemy import select

    from app.db import session_factory
    from app.logs import setup_logging
    from app.models import Job, JobFactsRow

    setup_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", default="facts,search,trust")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    with session_factory()() as s:
        q = select(Job.id).where(Job.is_active.is_(True)).order_by(Job.id)
        if not args.all:
            q = q.outerjoin(JobFactsRow, JobFactsRow.job_id == Job.id).where(JobFactsRow.job_id.is_(None))
        ids = list(s.execute(q).scalars())
        print(process_jobs(s, ids, steps=args.steps.split(",")), f"jobs={len(ids)}")


if __name__ == "__main__":
    main()
