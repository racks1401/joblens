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
