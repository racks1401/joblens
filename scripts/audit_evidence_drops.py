"""Audit evidence drops of past real-LLM extractions, replayed from llm_cache only (no API calls).

For each dropped value: is the quote really absent from the posting, or would a more lenient
normalization (Unicode NFKC, all punctuation removed, bullets) have matched? The second case means the
evidence check is too strict.

    python scripts/audit_evidence_drops.py --model openai/gpt-oss-20b --prompt facts_v1
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.db import session_factory  # noqa: E402
from app.extract.facts import (  # noqa: E402
    coerce_raw,
    evidence_ok,
    facts_schema_json,
    job_posting_text,
    norm_for_match,
)
from app.extract.prompting import build_messages  # noqa: E402
from app.llm.cache import DBCache  # noqa: E402
from app.llm.client import LLMClient, LLMResult  # noqa: E402
from app.models import Job, JobFactsRow  # noqa: E402
from app.schemas import EVIDENCED_FIELDS, JobFacts  # noqa: E402


class CacheOnly:
    name = "cache-only"

    def complete(self, **_: Any) -> LLMResult:
        raise RuntimeError("cache miss: this audit never calls the API")


def lenient(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower()
    s = re.sub(r"(?m)^\s*[-*•]\s+", "", s)
    return " ".join(re.sub(r"[^\w\s]", " ", s).split())


def longest_prefix(ev: str, text: str) -> int:
    lo, hi = 0, len(ev)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if ev[:mid] in text:
            lo = mid
        else:
            hi = mid - 1
    return lo


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="openai/gpt-oss-20b")
    ap.add_argument("--prompt", default="facts_v1")
    ap.add_argument("--out", default="data/private/evidence_drop_audit.jsonl")
    args = ap.parse_args()
    sf = session_factory()
    llm = LLMClient(CacheOnly(), args.model, DBCache(sf))  # type: ignore[arg-type]
    rows: list[dict[str, Any]] = []
    with sf() as s:
        jobs = s.execute(select(Job).join(JobFactsRow, JobFactsRow.job_id == Job.id)
                         .where(JobFactsRow.model == args.model).order_by(Job.id)).scalars().all()
        for j in jobs:
            text = job_posting_text(j, 8000)
            msgs = build_messages(args.prompt, json_schema=facts_schema_json(), job_text=text)
            raw, _ = llm.chat_json(msgs, temperature=0.0, prompt_version=args.prompt)
            f = JobFacts.model_validate(coerce_raw(raw, j.title, j.company or None))
            tn, tl = norm_for_match(text), lenient(text)
            items = [(k, getattr(f, k)) for k in EVIDENCED_FIELDS] + [("red_flags", x) for x in f.red_flags]
            for field, ev in items:
                v = ev.value.model_dump() if hasattr(ev.value, "model_dump") else ev.value
                if v in (None, [], "") or evidence_ok(ev.evidence, tn):
                    continue
                e = ev.evidence or ""
                if not e:
                    verdict = "no evidence given"
                elif lenient(e) and all(lenient(p) in tl for p in re.split(r"\.\.\.+|…", e) if lenient(p)):
                    verdict = "CHECK TOO STRICT (matches after lenient normalization)"
                else:
                    en = norm_for_match(e)
                    k = longest_prefix(en, tn)
                    verdict = f"quote not in posting (first {k}/{len(en)} chars match)"
                rows.append({"job_id": j.id, "title": j.title, "field": field, "value": v, "evidence": e,
                             "evidence_len": len(e), "verdict": verdict})
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in rows), encoding="utf-8")
    print(f"jobs={len(jobs)} drops={len(rows)} -> {args.out}")
    print(collections.Counter(r["verdict"].split(" (")[0] for r in rows))
    print(collections.Counter(r["field"] for r in rows))


if __name__ == "__main__":
    main()
