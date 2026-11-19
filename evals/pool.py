"""Build the E1 labeling pool: for each eval profile, the union of the top 20 from every method,
shuffled (fixed seed), with method names and scores hidden.

    python -m evals.pool              # writes evals/data/retrieval_pool.jsonl
    python -m evals.pool --recheck    # writes a 10% re-check sample of your gold labels
"""

from __future__ import annotations

import argparse
import random

from sqlalchemy.orm import Session

from app.search.service import load_jobs, retrieve
from app.search.text import build_profile_query_text, profile_keyword_query, structured_text
from evals.common import DATA, METHODS, job_key, load_profiles, read_jsonl, write_jsonl

SEED = 20261119
TOP = 20


def build_pool(s: Session, top: int = TOP, seed: int = SEED) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    rng = random.Random(seed)
    for pid, prof in load_profiles().items():
        qtext, kwq = build_profile_query_text(prof), profile_keyword_query(prof)
        ids: set[int] = set()
        for m in METHODS.values():
            ids |= {j for j, _ in retrieve(s, qtext, kwq, m["mode"], m["model"], m["variant"], limit=top)[:top]}
        loaded = load_jobs(s, sorted(ids))
        items = sorted(loaded.values(), key=lambda x: x.job.id)
        rng.shuffle(items)
        for it in items:
            j = it.job
            rows.append({
                "profile_id": pid, "job_key": job_key(j.source, j.source_job_id), "job_id": j.id,
                "title": j.title, "company": j.company, "location": j.location_raw, "url": j.url,
                "summary": structured_text(j.title, j.company or None, j.description_text, it.facts)[:900],
            })
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--recheck", action="store_true")
    args = ap.parse_args()
    if args.recheck:
        gold = read_jsonl(DATA / "retrieval_labels.jsonl")
        sample = random.Random(SEED).sample(gold, max(1, len(gold) // 10)) if gold else []
        n = write_jsonl(DATA / "retrieval_recheck.jsonl",
                        [{k: v for k, v in g.items() if k not in ("relevance", "reason")} for g in sample])
        print(f"wrote {n} items to re-label blind -> evals/data/retrieval_recheck.jsonl")
        return
    from app.db import session_factory

    with session_factory()() as s:
        rows = build_pool(s)
    n = write_jsonl(DATA / "retrieval_pool.jsonl", rows)
    per: dict[object, int] = {}
    for r in rows:
        per[r["profile_id"]] = per.get(r["profile_id"], 0) + 1
    print(f"wrote {n} pool items for {len(per)} profiles -> evals/data/retrieval_pool.jsonl {per}")


if __name__ == "__main__":
    main()
