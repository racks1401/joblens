"""One JSON-mode call to a model; prints latency, token usage and the provider's rate-limit headers.

    python scripts/probe_llm.py openai/gpt-oss-20b
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.extract.facts import facts_schema_json, posting_text  # noqa: E402
from app.extract.prompting import build_messages  # noqa: E402

TEXT = posting_text("Python Developer Intern", "Acme Labs", "Bengaluru", (
    "Duration: 6 months internship with PPO opportunity. Stipend: Rs 25,000 per month.\n"
    "Eligibility: B.Tech / MCA students, 2026 and 2027 batch, minimum 7.0 CGPA, all branches.\n"
    "Requirements:\n- Python, SQL and REST APIs\nNice to have:\n- Docker"))


def main() -> None:
    model = sys.argv[1]
    s = get_settings().env
    msgs = build_messages("facts_v1", json_schema=facts_schema_json(), job_text=TEXT)
    t0 = time.perf_counter()
    r = httpx.post(s.llm_base_url.rstrip("/") + "/chat/completions", timeout=90,
                   headers={"Authorization": f"Bearer {s.llm_api_key}"},
                   json={"model": model, "messages": msgs, "temperature": 0, "response_format": {"type": "json_object"},
                         **(json.loads(sys.argv[2]) if len(sys.argv) > 2 else {})})
    print("status", r.status_code, "seconds", round(time.perf_counter() - t0, 1))
    for k, v in r.headers.items():
        if k.startswith("x-ratelimit") or k == "retry-after":
            print(k, v)
    if r.status_code == 200:
        j = r.json()
        print("usage", j.get("usage"))
        print(j["choices"][0]["message"]["content"][:1500])
    else:
        print(r.text[:500])


if __name__ == "__main__":
    main()
