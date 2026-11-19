"""List models from an OpenAI-compatible provider configured in config/settings.yaml (GET {base_url}/models).

    python scripts/list_models.py              # groq
    python scripts/list_models.py --provider gemini

The API key is read from the provider's env var and never printed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from app.config import get_settings  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="groq")
    args = ap.parse_args()
    s = get_settings()
    base, key = s.provider_endpoint(args.provider)
    print("provider", args.provider, "base", base, "key set:", bool(key))
    r = httpx.get(base.rstrip("/") + "/models", headers={"Authorization": f"Bearer {key}"}, timeout=20)
    print("status", r.status_code)
    if r.status_code != 200:
        print(r.text[:300])
        return
    for m in sorted(r.json().get("data", []), key=lambda x: x.get("id", "")):
        ctx = m.get("context_window") or ""
        print(f"{m.get('id'):55s} owner={m.get('owned_by', '')!s:20s} ctx={ctx}")


if __name__ == "__main__":
    main()
