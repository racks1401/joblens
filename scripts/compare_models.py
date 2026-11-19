"""Compare two models' extractions on the jobs BOTH extracted, replayed from llm_cache only (no API calls).

Both are scored with the current evidence checker, on the same posting text and prompt version.
This measures schema validity and evidence grounding, not accuracy (that needs gold labels: E2).

    python scripts/compare_models.py --models openai/gpt-oss-20b,gemini-3.5-flash-lite
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import ValidationError  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db import session_factory  # noqa: E402
from app.extract.facts import check_evidence, coerce_raw, facts_schema_json, job_posting_text  # noqa: E402
from app.extract.prompting import build_messages  # noqa: E402
from app.llm.cache import DBCache  # noqa: E402
from app.llm.client import LLMClient, LLMJSONError  # noqa: E402
from app.models import Job  # noqa: E402
from app.schemas import JobFacts  # noqa: E402
from scripts.audit_evidence_drops import CacheOnly  # noqa: E402


def replay(llm: LLMClient, job: Job, prompt: str) -> dict[str, Any] | None:
    """First attempt of an extraction from cache; None if this model never extracted this text."""
    text = job_posting_text(job, 8000)
    msgs = build_messages(prompt, json_schema=facts_schema_json(), job_text=text)
    try:
        raw, _ = llm.chat_json(msgs, temperature=0.0, prompt_version=prompt)
    except RuntimeError:
        return None
    except LLMJSONError:
        return {"valid": False, "kept": 0, "dropped": 0}
    try:
        f = JobFacts.model_validate(coerce_raw(raw, job.title, job.company or None))
    except ValidationError:
        return {"valid": False, "kept": 0, "dropped": 0}
    f, dropped = check_evidence(f, text)
    kept = sum(1 for v in f.model_dump().values() if isinstance(v, dict) and v.get("value") not in (None, []))
    kept += len(f.red_flags)
    return {"valid": True, "kept": kept, "dropped": len(dropped)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="openai/gpt-oss-20b,gemini-3.5-flash-lite")
    ap.add_argument("--prompt", default="facts_v1")
    args = ap.parse_args()
    models = args.models.split(",")
    sf = session_factory()
    llms = {m: LLMClient(CacheOnly(), m, DBCache(sf)) for m in models}  # type: ignore[arg-type]
    res: dict[str, list[dict[str, Any]]] = {m: [] for m in models}
    with sf() as s:
        for job in s.execute(select(Job).order_by(Job.id)).scalars():
            outs = {m: replay(llms[m], job, args.prompt) for m in models}
            if all(o is not None for o in outs.values()):
                for m in models:
                    res[m].append(outs[m])  # type: ignore[arg-type]
    n = len(res[models[0]])
    print(f"jobs extracted by both models (same text, prompt {args.prompt}): {n}")
    print(f"{'model':28s} {'valid':>9s} {'kept fields':>12s} {'dropped':>8s} {'drop rate':>10s} {'jobs w/ drop':>13s}")
    for m in models:
        r = res[m]
        v = sum(x["valid"] for x in r)
        k = sum(x["kept"] for x in r)
        d = sum(x["dropped"] for x in r)
        jd = sum(1 for x in r if x["dropped"])
        rate = d / (k + d) if k + d else 0.0
        print(f"{m:28s} {v:>4d}/{n:<4d} {k:>12d} {d:>8d} {rate:>9.1%} {jd:>6d}/{n:<6d}")


if __name__ == "__main__":
    main()
