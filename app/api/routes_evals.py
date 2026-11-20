"""GET /evals/latest: latest metrics per suite (from evals/results/*.json, the committed source of truth)."""

from __future__ import annotations

import json
import re
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


MANUAL_RX = re.compile(r"\*\*Manual result: ([^*]+?)\.?\*\*")


def _read(name: str) -> dict[str, Any] | None:
    p = RESULTS / name
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None  # type: ignore[no-any-return]
    except (OSError, ValueError):
        return None


@router.get("/evals/summary")
def evals_summary() -> dict[str, Any]:
    """Numbers the Evals page shows, read from the committed evals/results files, each with its sample size and
    caveat. Manual-review counts are parsed from each run's manual_review.md (the reviewer's own summary line)."""
    e1 = _read("retrieval_e1.json")
    if e1:
        groups = {}
        for g, methods in e1["metrics"].items():
            groups[g] = {m: {"mean": v["mean"], "profiles": v.get("profiles"),
                             "unlabeled_in_top10": v.get("unlabeled_in_top10"),
                             "labels": sum(p.get("labels", 0) for p in v.get("per_profile", {}).values())}
                         for m, v in methods.items() if isinstance(v, dict) and "mean" in v}
        e1 = {"k": e1["config"].get("k"), "assumption": e1["config"].get("assumption"),
              "label_sources_with_drafts": e1["config"].get("label_sources_b"), "groups": groups,
              "created_at": e1.get("created_at"),
              "caveat": "One reviewer, 5 profiles, at most ~25 labels per profile; differences of a few points "
                        "between methods are not meaningful."}
    runs = []
    for d in sorted(RESULTS.glob("agent_run*")):
        s = _read(f"{d.name}/agent_p00.json")
        if not s:
            continue
        ran = [c for c in s["conversations"] if c.get("ran")]
        mr = d / "manual_review.md"
        m = MANUAL_RX.search(mr.read_text(encoding="utf-8")) if mr.exists() else None
        runs.append({"run": d.name, "conversations": len(ran), "turns": sum(len(c["turns"]) for c in ran),
                     "grounded": sum(1 for c in ran if c["grounded"]),
                     "grounded_tools_only": sum(1 for c in ran if c.get("grounded_tools_only", c["grounded"])),
                     "not_run": [c["id"] for c in s["conversations"] if not c.get("ran")],
                     "total_tokens": s.get("total_tokens"),
                     "manual_review": m.group(1).strip() if m else None,
                     "manual_review_file": f"evals/results/{d.name}/manual_review.md" if m else None})
    return {"retrieval_e1": e1, "trust_classifier": _read("trust_classifier.json"),
            "label_agreement": _read("retrieval_agreement.json"), "agent_runs": runs,
            "agent_caveat": "One eval profile (p00), 15 fixed conversations, one model "
                            "(Groq gpt-oss-120b); the grounding check reads ids, numbers, dates, verdicts and rule "
                            "names, not free sentences."}
