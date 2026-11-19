"""GET /evals/latest: latest metrics per suite (from evals/results/*.json, the committed source of truth)."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter

from app.config import ROOT

router = APIRouter(tags=["evals"])
RESULTS = ROOT / "evals" / "results"


@router.get("/evals/latest")
def evals_latest() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for p in sorted(RESULTS.glob("*.json")):
        try:
            run = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(run, dict) and "suite" in run:
            md = p.with_suffix(".md")
            run["markdown"] = md.read_text(encoding="utf-8") if md.exists() else None
            out[run["suite"]] = run
    return {"suites": out}
