"""Run text files through POST /jobs/paste (real app, real LLM per settings) and print facts,
eligibility for a saved profile, and trust status. Never writes eval/gold files.

    python scripts/paste_files.py data/pasted/*.txt --profile <uuid>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.db import session_factory  # noqa: E402
from app.eligibility import check_eligibility  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Job, ProfileRow  # noqa: E402
from app.schemas import JobFacts, Profile  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--profile", required=True)
    args = ap.parse_args()
    client = TestClient(create_app())
    with session_factory()() as s:
        prow = s.get(ProfileRow, __import__("uuid").UUID(args.profile))
        assert prow is not None, "profile not found"
        prof = Profile.model_validate(prow.data)
    for f in args.files:
        text = Path(f).read_text(encoding="utf-8")
        print(f"\n################ {f} ({len(text)} chars)")
        if len(text.strip()) < 20:
            print("SKIPPED: file is empty or too short for /jobs/paste (min 20 chars).")
            continue
        m = re.search(r"^Company:\s*(.+)$", text, re.M)
        r = client.post("/jobs/paste", json={"text": text, "company": m.group(1).strip() if m else None})
        if r.status_code != 200:
            print("ERROR", r.status_code, r.text[:300])
            continue
        job = r.json()
        with session_factory()() as s:
            row = s.get(Job, job["id"])
            assert row is not None
            meta = {"job_id": row.id, "title": row.title, "company": row.company, "in_scope": row.in_scope,
                    "seniority": row.seniority_reason, "location_rule": row.location_reason,
                    "role_family": f"{row.role_family} ({row.role_reason})"}
        print("JOB:", json.dumps(meta, ensure_ascii=False))
        facts = JobFacts.model_validate(job["facts"]) if job["facts"] else None
        print(f"FACTS (model per job_facts row; valid={job['facts_valid']}):")
        for k, v in (job["facts"] or {}).items():
            if isinstance(v, dict) and "value" in v:
                print(f"  {k:22s} = {json.dumps(v['value'], ensure_ascii=False)}" + (f"   <- \"{v['evidence']}\"" if v["evidence"] else ""))
            elif k == "red_flags":
                print(f"  red_flags              = {json.dumps(v, ensure_ascii=False)}")
        if facts:
            e = check_eligibility(prof, facts)
            print(f"ELIGIBILITY for your profile: {e.verdict}")
            for rr in e.rules:
                print(f"  {rr.status:9s} {rr.rule:16s} {rr.message}" + (f'   <- "{rr.evidence}"' if rr.evidence else ""))
        print("TRUST:", job["trust"] if job["trust"] else "not computed - trust scoring (phase P5) is not built yet")


if __name__ == "__main__":
    main()
