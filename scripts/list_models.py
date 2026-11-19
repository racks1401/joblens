"""List models available from the configured OpenAI-compatible provider (GET {LLM_BASE_URL}/models).

    python scripts/list_models.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from app.config import get_settings  # noqa: E402


def main() -> None:
    s = get_settings().env
    r = httpx.get(s.llm_base_url.rstrip("/") + "/models", headers={"Authorization": f"Bearer {s.llm_api_key}"},
                  timeout=20)
    print("status", r.status_code)
    if r.status_code != 200:
        print(r.text[:300])
        return
    for m in sorted(r.json().get("data", []), key=lambda x: x.get("id", "")):
        ctx = m.get("context_window") or ""
        print(f"{m.get('id'):55s} owner={m.get('owned_by', '')!s:20s} ctx={ctx} active={m.get('active', '')}")


if __name__ == "__main__":
    main()
