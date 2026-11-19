"""Build a draft Profile from a resume PDF with the configured LLM (same code as POST /profiles/from-resume).

    python scripts/profile_from_resume.py data/private/resume.pdf [--out data/private/profile_draft.json]

The draft is written to data/private/ (gitignored). Review it, then save it with POST /profiles.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.extract.profile import extract_profile, pdf_to_text  # noqa: E402
from app.llm.client import get_llm  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--out", default="data/private/profile_draft.json")
    args = ap.parse_args()
    text = pdf_to_text(Path(args.pdf).read_bytes())
    llm = get_llm("profile")
    draft = extract_profile(text, llm)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"model": llm.model, "resume_chars": len(text), "draft": draft.model_dump(mode="json")},
                              indent=2), encoding="utf-8")
    print(f"model={llm.model} resume_chars={len(text)} -> {out}")
    print(json.dumps(draft.model_dump(mode="json"), indent=2))


if __name__ == "__main__":
    main()
