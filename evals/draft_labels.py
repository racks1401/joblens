"""Machine-drafted labels for REVIEW ONLY. Output goes to evals/data/drafts/ with "status": "draft".

The drafting model must differ from the pipeline's models (default: the judge model, a different
family). Drafts are never written to gold files and are never hand labels. Resumable: items already
drafted are skipped, so re-run after a provider daily limit.

    python -m evals.draft_labels --suite retrieval
    python -m evals.draft_labels --suite extraction
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import UTC, datetime
from typing import Any

from app.config import get_settings
from app.llm.client import DailyLimitReached, LLMClient, LLMJSONError, get_llm
from evals.common import DATA, DRAFTS, job_key, load_profiles, read_jsonl
from evals.pool import SEED

RETRIEVAL_SYSTEM = """You draft relevance labels for a job-matching evaluation. For each job decide how relevant it is for the candidate:
2 = would apply: role and core skills fit, and the candidate is eligible or likely eligible.
1 = related but a stretch: partial skill fit, or eligibility unclear.
0 = not relevant, or clearly ineligible.
Rules: a candidate with 0-1 years of experience gets 0 for roles requiring 3+ years and at most 1 for 1-2 years.
Senior, lead, staff, principal or manager roles are 0 for freshers. A job outside the candidate's locations and
work modes is at most 1. Judge only from the text given. Give a short reason (max 15 words) for every job.
Output JSON only: {"labels": [{"i": <job number>, "relevance": 0|1|2, "reason": "..."}]} with one entry per job."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _append(path: Any, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _profile_text(p: Any) -> str:
    return (f"Degree {p.degree} ({p.specialization or '-'}), graduating {p.graduation_year}, "
            f"{p.experience_years:g} years experience, CGPA {p.cgpa if p.cgpa is not None else 'not set'}.\n"
            f"Target roles: {', '.join(p.target_roles)}\nSkills: {', '.join(s.name for s in p.skills)}\n"
            f"Locations: {', '.join(p.preferred_locations) or 'any'}; work modes: {', '.join(p.work_modes)}; "
            f"wants: {', '.join(p.employment_types)}")


def draft_retrieval(llm: LLMClient, batch: int) -> dict[str, int]:
    out = DRAFTS / "retrieval_labels_draft.jsonl"
    done = {(r["profile_id"], r["job_key"]) for r in read_jsonl(out)}
    pool = [r for r in read_jsonl(DATA / "retrieval_pool.jsonl") if (r["profile_id"], r["job_key"]) not in done]
    profiles = load_profiles()
    by_p: dict[str, list[dict[str, Any]]] = {}
    for r in pool:
        by_p.setdefault(r["profile_id"], []).append(r)
    written = failed = 0
    for pid, items in by_p.items():
        for i in range(0, len(items), batch):
            chunk = items[i:i + batch]
            jobs = "\n\n".join(f"[{n}] {r['title']} | {r['company']} | {r['location'] or '-'}\n{r['summary'][:420]}"
                               for n, r in enumerate(chunk, 1))
            msgs = [{"role": "system", "content": RETRIEVAL_SYSTEM},
                    {"role": "user", "content": f"Candidate:\n{_profile_text(profiles[pid])}\n\nJobs:\n{jobs}"}]
            try:
                data, res = llm.chat_json(msgs, temperature=0.0, prompt_version="draft_retrieval_v1")
            except LLMJSONError:
                failed += len(chunk)
                continue
            got = {int(x["i"]): x for x in data.get("labels", []) if isinstance(x, dict) and str(x.get("i", "")).isdigit()}
            rows = []
            for n, r in enumerate(chunk, 1):
                lab = got.get(n)
                if not lab or lab.get("relevance") not in (0, 1, 2):
                    failed += 1
                    continue
                rows.append({"profile_id": pid, "job_key": r["job_key"], "job_id": r["job_id"],
                             "relevance": lab["relevance"], "reason": str(lab.get("reason", ""))[:200],
                             "status": "draft", "labeler": f"{res.model} (machine draft, not reviewed)",
                             "drafted_at": _now()})
            _append(out, rows)
            written += len(rows)
            print(f"retrieval {pid}: +{len(rows)} (total new {written})", flush=True)
    return {"written": written, "failed": failed, "already_done": len(done)}


def select_extraction_jobs(n_ats: int = 30) -> list[dict[str, Any]]:
    """30 ATS jobs (seeded; half fresher-relevant titles) + the synthetic pasted posts, as text snapshots."""
    import re

    from sqlalchemy import select

    from app.db import session_factory
    from app.extract.facts import job_posting_text
    from app.models import Job

    rng = random.Random(SEED)
    with session_factory()() as s:
        jobs = list(s.execute(select(Job).where(Job.is_active.is_(True)).order_by(Job.id)).scalars())
        fresh = [j for j in jobs if re.search(r"intern|graduate|fresher|entry|junior|trainee|associate|new grad", j.title, re.I)]
        rest = [j for j in jobs if j not in fresh]
        picked = rng.sample(fresh, min(len(fresh), n_ats // 2))
        picked += rng.sample(rest, n_ats - len(picked))
        items = [{"job_key": job_key(j.source, j.source_job_id), "job_id": j.id, "title": j.title,
                  "company": j.company or None, "text": job_posting_text(j, get_settings().cfg.llm.extract_max_chars)}
                 for j in picked]
    for p in read_jsonl(DRAFTS / "paste_jobs_synthetic.jsonl"):
        items.append({"job_key": p["job_key"], "job_id": None, "title": p["title"], "company": p["company"] or None,
                      "text": p["text"], "synthetic": True})
    return items


def draft_extraction(llm: LLMClient, prompt_version: str) -> dict[str, int]:
    from app.extract.facts import extract_facts

    out = DRAFTS / "extraction_gold_draft.jsonl"
    done = {r["job_key"] for r in read_jsonl(out)}
    written = 0
    for it in select_extraction_jobs():
        if it["job_key"] in done:
            continue
        r = extract_facts(it["text"], it["title"], it["company"], llm, prompt_version)
        facts = r.facts.model_dump(mode="json")
        fields = [k for k, v in facts.items() if isinstance(v, dict) and "value" in v]
        gold = {k: facts[k]["value"] for k in fields}
        reasons = {k: (f"quote: {facts[k]['evidence']}" if facts[k]["value"] is not None else "not stated in posting")
                   for k in fields}
        _append(out, [{**{k: it.get(k) for k in ("job_key", "job_id", "title", "company", "text", "synthetic")},
                       "gold": gold, "reasons": reasons, "status": "draft",
                       "labeler": f"{r.model} (machine draft, not reviewed)", "drafted_at": _now(),
                       "draft_valid": r.valid, "draft_dropped_fields": r.dropped}])
        written += 1
        print(f"extraction +1 {it['job_key']} (total new {written})", flush=True)
    return {"written": written, "already_done": len(done)}


def main() -> None:
    from app.logs import setup_logging

    setup_logging("WARNING")
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", choices=["retrieval", "extraction"], required=True)
    ap.add_argument("--batch", type=int, default=15)
    ap.add_argument("--prompt", default="facts_v2")
    args = ap.parse_args()
    st = get_settings()
    llm = get_llm("judge")
    pipeline_models = {st.cfg.models.extract, st.cfg.models.explain, st.cfg.models.agent}
    if llm.model in pipeline_models:
        raise SystemExit(f"draft model {llm.model} is also a pipeline model; set JUDGE_MODEL to a different one")
    try:
        res = draft_retrieval(llm, args.batch) if args.suite == "retrieval" else draft_extraction(llm, args.prompt)
    except DailyLimitReached as e:
        print(f"STOPPED: provider daily limit ({e}). Re-run the same command later to resume.")
        return
    print(res)


if __name__ == "__main__":
    main()
