"""Review draft retrieval labels one pair at a time and save human labels.

    python scripts/review_labels.py --profile p00
    python scripts/review_labels.py --profile p00 --sample 20      # + 20 random pairs from other profiles

The jobs' facts and posting text come from the database in DATABASE_URL. For the local dev DB use
    $env:DATABASE_URL="postgresql+psycopg://postgres@127.0.0.1:54329/joblens"
If the database cannot be read the tool stops (it would otherwise show only short summaries); pass
--allow-no-db to review anyway.

For each pair it shows the job (title, company, location), its key requirements with the evidence quote,
every line of the raw posting that mentions years or experience, your profile summary, and the draft
relevance + reason. When a job has no extracted facts it says so and shows the first 1,500 characters of
the raw posting. Keys:
    0 / 1 / 2  set the label        Enter  keep the draft label        s  skip        q  quit

Answers are appended to evals/data/retrieval_labels_reviewed.jsonl with status "human_reviewed" and a
timestamp (resume-safe: pairs already reviewed are not shown again). The drafts file is only ever read.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.common import DATA, DRAFTS, load_profiles, read_jsonl  # noqa: E402

REVIEWED = DATA / "retrieval_labels_reviewed.jsonl"
RAW_CHARS = 1500
YEAR_LINE = re.compile(r"(?i)\byears?\b|\byrs?\b|experience")
REQ_FIELDS = [("employment_type", "Type"), ("experience_min_years", "Experience (min yrs)"),
              ("degrees_allowed", "Degrees"), ("specializations", "Branches"), ("graduation_years", "Batch"),
              ("min_cgpa", "Min CGPA"), ("locations", "Locations"), ("work_mode", "Mode"), ("pay", "Pay"),
              ("required_skills", "Required skills")]


def latest_drafts(path: Path = DRAFTS / "retrieval_labels_draft.jsonl") -> dict[tuple[str, str], dict[str, Any]]:
    """Last draft per (profile_id, job_key)."""
    return {(d["profile_id"], d["job_key"]): d for d in read_jsonl(path)}


def load_jobs(job_keys: list[str]) -> tuple[dict[str, dict[str, Any]], str | None, str]:
    """({job_key: {"facts": dict | None, "text": str}}, error or None, database host/db label)."""
    from app.config import get_settings

    url = get_settings().env.database_url
    label = url.split("@")[-1]
    try:
        from sqlalchemy import select

        from app.db import session_factory
        from app.models import Job, JobFactsRow

        wanted = set(job_keys)
        out: dict[str, dict[str, Any]] = {}
        with session_factory()() as s:
            q = select(Job, JobFactsRow.facts).outerjoin(JobFactsRow, JobFactsRow.job_id == Job.id)
            for job, facts in s.execute(q):
                k = f"{job.source}:{job.source_job_id}"
                if k in wanted:
                    out[k] = {"facts": facts, "text": job.description_text or ""}
        return out, None, label
    except Exception as e:  # noqa: BLE001 - reported loudly by main()
        return {}, f"{type(e).__name__}: {str(e).splitlines()[0][:200]}", label


def profile_summary(p: Any) -> str:
    return (f"{p.degree} ({p.specialization or '-'}), graduating {p.graduation_year}, {p.experience_years:g} yrs, "
            f"CGPA {p.cgpa if p.cgpa is not None else 'n/a'} | roles: {', '.join(p.target_roles)} | "
            f"locations: {', '.join(p.preferred_locations) or 'any'} ({', '.join(p.work_modes)}) | "
            f"skills: {', '.join(s.name for s in p.skills[:15])}")


def year_lines(text: str, limit: int = 12) -> list[str]:
    """Lines of the raw posting that mention years or experience (to verify claims like '2+ years')."""
    return [ln.strip()[:220] for ln in text.split("\n") if YEAR_LINE.search(ln)][:limit]


def render(item: dict[str, Any], draft: dict[str, Any], job: dict[str, Any] | None, profile: str, n: int,
           total: int) -> str:
    lines = [f"\n[{n}/{total}] {item['profile_id']}  {item['title']}  |  {item['company']}  |  {item.get('location') or '-'}",
             f"  Profile: {profile}"]
    facts, text = (job or {}).get("facts"), (job or {}).get("text") or ""
    if job is None:
        lines.append("  !! JOB NOT FOUND IN THE DATABASE - showing the short pool summary only:")
        lines.append("     " + item.get("summary", "")[:400].replace("\n", " | "))
    elif not facts:
        lines.append("  !! NO EXTRACTED FACTS FOR THIS JOB - check the raw posting below.")
        lines.append(f"  Raw posting (first {RAW_CHARS} chars):")
        lines += ["     " + ln for ln in text[:RAW_CHARS].split("\n")]
    else:
        lines.append("  Requirements (extracted, with evidence):")
        for key, label in REQ_FIELDS:
            f = facts.get(key) or {}
            if f.get("value") not in (None, []):
                v = f["value"]
                v = ", ".join(map(str, v)) if isinstance(v, list) else (json.dumps(v) if isinstance(v, dict) else v)
                lines.append(f"    {label:20s} {v}   <- \"{(f.get('evidence') or '')[:110]}\"")
    if job is not None:
        yl = year_lines(text)
        lines.append("  Posting lines mentioning years/experience:" + ("" if yl else " (none)"))
        lines += [f"    > {ln}" for ln in yl]
    lines.append(f"  Draft: relevance {draft['relevance']} - {draft['reason']}"
                 + ("   [low confidence draft]" if draft.get("low_confidence") else ""))
    lines.append("  Label [0/1/2, Enter=keep draft, s=skip, q=quit]: ")
    return "\n".join(lines)


def review(items: list[dict[str, Any]], drafts: dict[tuple[str, str], dict[str, Any]],
           jobs: dict[str, dict[str, Any]], profiles: dict[str, Any], out_path: Path,
           ask: Callable[[str], str] = input) -> dict[str, int]:
    """Interactive loop. Appends one line per answered pair; returns counts."""
    done = {(r["profile_id"], r["job_key"]) for r in read_jsonl(out_path)}
    todo = [it for it in items if (it["profile_id"], it["job_key"]) not in done and (it["profile_id"], it["job_key"]) in drafts]
    counts = {"labelled": 0, "kept": 0, "skipped": 0}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    for n, it in enumerate(todo, 1):
        key = (it["profile_id"], it["job_key"])
        d = drafts[key]
        job = jobs.get(it["job_key"])
        prompt = render(it, d, job, profile_summary(profiles[it["profile_id"]]), n, len(todo))
        while True:
            ans = ask(prompt).strip().lower()
            if ans in ("", "0", "1", "2", "s", "q"):
                break
            prompt = "  Please type 0, 1, 2, Enter, s or q: "
        if ans == "q":
            break
        if ans == "s":
            counts["skipped"] += 1
            continue
        rel = d["relevance"] if ans == "" else int(ans)
        row = {"profile_id": it["profile_id"], "job_key": it["job_key"], "job_id": it.get("job_id"), "relevance": rel,
               "reason": d["reason"] if ans == "" or rel == d["relevance"] else f"human: changed from draft {d['relevance']}",
               "draft_relevance": d["relevance"], "draft_reason": d["reason"], "decision": "kept_draft" if ans == "" else "set",
               "shown": "facts" if job and job.get("facts") else ("raw_posting" if job else "summary_only"),
               "status": "human_reviewed", "reviewed_at": datetime.now(UTC).isoformat(timespec="seconds")}
        with out_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        counts["kept" if ans == "" else "labelled"] += 1
    return counts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="p00")
    ap.add_argument("--sample", type=int, default=0, help="also N random pairs from other profiles")
    ap.add_argument("--pool", default="retrieval_pool_v4.jsonl")
    ap.add_argument("--seed", type=int, default=20261119)
    ap.add_argument("--allow-no-db", action="store_true", help="review even if job facts/text cannot be read")
    args = ap.parse_args()
    pool = read_jsonl(DATA / args.pool)
    items = [r for r in pool if r["profile_id"] == args.profile]
    others = [r for r in pool if r["profile_id"] != args.profile]
    if args.sample:
        items += random.Random(args.seed).sample(others, min(args.sample, len(others)))
    keys = [r["job_key"] for r in items]
    jobs, err, label = load_jobs(keys)
    found = sum(1 for k in set(keys) if k in jobs)
    print(f"Database: {label} -> {found}/{len(set(keys))} jobs found" + (f" (error: {err})" if err else ""))
    if (err or found == 0) and not args.allow_no_db:
        print("\nSTOP: job facts and posting text could not be read, so reviews would rest on short summaries.\n"
              "For the local dev DB, run in the same PowerShell window:\n"
              '  $env:DATABASE_URL="postgresql+psycopg://postgres@127.0.0.1:54329/joblens"\n'
              "then start the tool again (or pass --allow-no-db to review anyway).")
        sys.exit(2)
    counts = review(items, latest_drafts(), jobs, load_profiles(), REVIEWED)
    print(f"\n{counts} -> {REVIEWED}")


if __name__ == "__main__":
    main()
