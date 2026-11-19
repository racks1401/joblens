"""One JSON-mode extraction call; prints latency, usage, rate-limit headers and a check of the output.

    python scripts/probe_llm.py openai/gpt-oss-20b --extra '{"reasoning_effort": "low"}'
    python scripts/probe_llm.py gemini-3.5-flash-lite --provider gemini

The API key is read from the provider's env var and never printed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.extract.facts import check_evidence, coerce_raw, facts_schema_json, posting_text  # noqa: E402
from app.extract.prompting import build_messages  # noqa: E402
from app.schemas import JobFacts  # noqa: E402

TEXT = posting_text("Python Developer Intern", "Acme Labs", "Bengaluru", (
    "Duration: 6 months internship with PPO opportunity. Stipend: Rs 25,000 per month.\n"
    "Eligibility: B.Tech / MCA students, 2026 and 2027 batch, minimum 7.0 CGPA, all branches.\n"
    "Requirements:\n- Python, SQL and REST APIs\nNice to have:\n- Docker"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("--provider", default="groq")
    ap.add_argument("--extra", default="{}", help="extra JSON body fields")
    args = ap.parse_args()
    base, key = get_settings().provider_endpoint(args.provider)
    msgs = build_messages("facts_v1", json_schema=facts_schema_json(), job_text=TEXT)
    t0 = time.perf_counter()
    r = httpx.post(base.rstrip("/") + "/chat/completions", timeout=90, headers={"Authorization": f"Bearer {key}"},
                   json={"model": args.model, "messages": msgs, "temperature": 0,
                         "response_format": {"type": "json_object"}, **json.loads(args.extra)})
    print("status", r.status_code, "seconds", round(time.perf_counter() - t0, 1))
    for k, v in r.headers.items():
        if "ratelimit" in k or k == "retry-after":
            print(k, v)
    if r.status_code != 200:
        print(r.text[:600])
        return
    j = r.json()
    print("usage", j.get("usage"))
    content = j["choices"][0]["message"]["content"]
    try:
        facts = JobFacts.model_validate(coerce_raw(json.loads(content), "Python Developer Intern", "Acme Labs"))
        facts, dropped = check_evidence(facts, TEXT)
        kept = {k: v["value"] for k, v in facts.model_dump(mode="json").items() if isinstance(v, dict) and v.get("value") is not None}
        print("schema valid; dropped:", dropped)
        print("kept:", kept)
    except Exception as e:  # noqa: BLE001
        print("INVALID:", type(e).__name__, str(e)[:300])
        print(content[:800])


if __name__ == "__main__":
    main()
