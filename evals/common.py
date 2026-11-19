"""Shared eval helpers: data files, profiles, retrieval methods, results writing."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import ROOT
from app.extract.profile import normalize_profile
from app.schemas import Profile

DATA = ROOT / "evals" / "data"
DRAFTS = DATA / "drafts"
RESULTS = ROOT / "evals" / "results"

# E1 methods (spec 15.3). M5 = hybrid RRF of keyword + the best vector method (chosen at run time).
METHODS: dict[str, dict[str, Any]] = {
    "M1": {"name": "keyword", "mode": "keyword", "model": None, "variant": None},
    "M2": {"name": "vector MiniLM raw", "mode": "vector", "model": "minilm", "variant": "raw"},
    "M3": {"name": "vector MiniLM structured", "mode": "vector", "model": "minilm", "variant": "structured"},
    "M4": {"name": "vector bge-small structured", "mode": "vector", "model": "bge-small", "variant": "structured"},
    "M5": {"name": "hybrid RRF", "mode": "hybrid", "model": None, "variant": None},
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = list(rows)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return len(rows)


def load_profiles(path: Path | None = None) -> dict[str, Profile]:
    """Eval profiles keyed by profile_id, normalized like saved profiles."""
    return {r["profile_id"]: normalize_profile(Profile.model_validate(r["profile"]))
            for r in read_jsonl(path or DATA / "profiles.jsonl")}


def job_key(source: str, source_job_id: str) -> str:
    return f"{source}:{source_job_id}"


def git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              timeout=10).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def labels_path(gold_name: str, labels: str) -> Path:
    """Gold file, or the draft file when --labels drafts."""
    if labels == "drafts":
        stem = gold_name.rsplit(".", 1)[0]
        return DRAFTS / f"{stem}_draft.jsonl"
    return DATA / gold_name


def save_results(suite: str, config: dict[str, Any], metrics: dict[str, Any], markdown: str) -> Path:
    """Write evals/results/{suite}.json + .md and an eval_runs row (if the DB is reachable)."""
    RESULTS.mkdir(parents=True, exist_ok=True)
    run = {"suite": suite, "created_at": datetime.now(UTC).isoformat(timespec="seconds"), "git_commit": git_commit(),
           "config": config, "metrics": metrics}
    out = RESULTS / f"{suite}.json"
    out.write_text(json.dumps(run, indent=1, default=str), encoding="utf-8")
    (RESULTS / f"{suite}.md").write_text(markdown, encoding="utf-8")
    try:
        from app.db import session_factory
        from app.models import EvalRun

        with session_factory()() as s:
            s.add(EvalRun(suite=suite, config=config, metrics=json.loads(json.dumps(metrics, default=str)),
                          git_commit=run["git_commit"]))
            s.commit()
    except Exception:  # noqa: BLE001 - results files are the source of truth
        pass
    return out


def md_table(headers: list[str], rows: list[list[Any]]) -> str:
    def fmt(x: Any) -> str:
        if isinstance(x, float):
            return f"{x:.3f}"
        return "—" if x is None else str(x)

    return "\n".join(["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
                     + ["| " + " | ".join(fmt(c) for c in r) + " |" for r in rows])
