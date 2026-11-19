"""Review draft retrieval labels one pair at a time and save human labels.

    python scripts/review_labels.py --profile p00
    python scripts/review_labels.py --profile p00 --sample 20      # + 20 random pairs from other profiles

For each pair it shows the job (title, company, location), its key requirements with the evidence quote
from the posting, your profile summary, and the draft relevance + reason. Keys:
    0 / 1 / 2  set the label        Enter  keep the draft label        s  skip        q  quit

Answers are appended to evals/data/retrieval_labels_reviewed.jsonl with status "human_reviewed" and a
timestamp (resume-safe: pairs already reviewed are not shown again). The drafts file is only ever read.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.common import DATA, DRAFTS, load_profiles, read_jsonl  # noqa: E402

REVIEWED = DATA / "retrieval_labels_reviewed.jsonl"
REQ_FIELDS = [("employment_type", "Type"), ("experience_min_years", "Experience (min yrs)"),
              ("degrees_allowed", "Degrees"), ("specializations", "Branches"), ("graduation_years", "Batch"),
              ("min_cgpa", "Min CGPA"), ("locations", "Locations"), ("work_mode", "Mode"), ("pay", "Pay"),
              ("required_skills", "Required skills")]


def latest_drafts(path: Path = DRAFTS / "retrieval_labels_draft.jsonl") -> dict[tuple[str, str], dict[str, Any]]:
    """Last draft per (profile_id, job_key)."""
    return {(d["profile_id"], d["job_key"]): d for d in read_jsonl(path)}


def facts_for(job_keys: list[str]) -> dict[str, dict[str, Any]]:
    """JobFacts per job_key from the DB ({} if the DB is unreachable)."""
    try:
        from sqlalchemy import select

        from app.db import session_factory
        from app.models import Job, JobFactsRow

        out = {}
        with session_factory()() as s:
            for job, facts in s.execute(select(Job, JobFactsRow.facts).join(JobFactsRow, JobFactsRow.job_id == Job.id)):
                k = f"{job.source}:{job.source_job_id}"
                if k in job_keys:
                    out[k] = facts
        return out
    except Exception:  # noqa: BLE001 - the tool still works from the pool summary
        return {}


def profile_summary(p: Any) -> str:
    return (f"{p.degree} ({p.specialization or '-'}), graduating {p.graduation_year}, {p.experience_years:g} yrs, "
            f"CGPA {p.cgpa if p.cgpa is not None else 'n/a'} | roles: {', '.join(p.target_roles)} | "
            f"locations: {', '.join(p.preferred_locations) or 'any'} ({', '.join(p.work_modes)}) | "
            f"skills: {', '.join(s.name for s in p.skills[:15])}")


def render(item: dict[str, Any], draft: dict[str, Any], facts: dict[str, Any] | None, profile: str, n: int,
           total: int) -> str:
    lines = [f"\n[{n}/{total}] {item['profile_id']}  {item['title']}  |  {item['company']}  |  {item.get('location') or '-'}",
             f"  Profile: {profile}", "  Requirements:"]
    if facts:
        for key, label in REQ_FIELDS:
            f = facts.get(key) or {}
            if f.get("value") not in (None, []):
                v = f["value"]
                v = ", ".join(map(str, v)) if isinstance(v, list) else (json.dumps(v) if isinstance(v, dict) else v)
                lines.append(f"    {label:20s} {v}   <- \"{(f.get('evidence') or '')[:110]}\"")
    else:
        lines.append("    (no extracted facts available; summary:) " + item.get("summary", "")[:300].replace("\n", " | "))
    lines.append(f"  Draft: relevance {draft['relevance']} - {draft['reason']}")
    lines.append("  Label [0/1/2, Enter=keep draft, s=skip, q=quit]: ")
    return "\n".join(lines)


def review(items: list[dict[str, Any]], drafts: dict[tuple[str, str], dict[str, Any]],
           facts: dict[str, dict[str, Any]], profiles: dict[str, Any], out_path: Path,
           ask: Callable[[str], str] = input) -> dict[str, int]:
    """Interactive loop. Appends one line per answered pair; returns counts."""
    done = {(r["profile_id"], r["job_key"]) for r in read_jsonl(out_path)}
    todo = [it for it in items if (it["profile_id"], it["job_key"]) not in done and (it["profile_id"], it["job_key"]) in drafts]
    counts = {"labelled": 0, "kept": 0, "skipped": 0}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    for n, it in enumerate(todo, 1):
        key = (it["profile_id"], it["job_key"])
        d = drafts[key]
        prompt = render(it, d, facts.get(it["job_key"]), profile_summary(profiles[it["profile_id"]]), n, len(todo))
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
    args = ap.parse_args()
    pool = read_jsonl(DATA / args.pool)
    items = [r for r in pool if r["profile_id"] == args.profile]
    others = [r for r in pool if r["profile_id"] != args.profile]
    if args.sample:
        items += random.Random(args.seed).sample(others, min(args.sample, len(others)))
    drafts = latest_drafts()
    counts = review(items, drafts, facts_for([r["job_key"] for r in items]), load_profiles(), REVIEWED)
    print(f"\n{counts} -> {REVIEWED}")


if __name__ == "__main__":
    main()
