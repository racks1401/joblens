"""Eval runner (spec 15.1).

    python -m evals.run --suite retrieval   --variants all
    python -m evals.run --suite extraction  --prompt facts_v1,facts_v2 --model openai/gpt-oss-20b
    python -m evals.run --suite all --report README_EVALS.md
    add --labels drafts to run against the unreviewed drafts in evals/data/drafts/ (results marked DRAFT)

Results: evals/results/{suite}.json + .md, and a row in eval_runs. LLM calls are cached (llm_cache),
so a re-run with the same model + prompt is free and repeatable.
"""

from __future__ import annotations

import argparse
import statistics
import time
from collections.abc import Callable
from typing import Any, cast

from app.config import ROOT, get_settings
from evals import metrics as M
from evals.common import METHODS, RESULTS, labels_path, load_profiles, md_table, read_jsonl, save_results

DRAFT_BANNER = ("> **DRAFT LABELS — machine-drafted, not reviewed by a human. Not a real result.** "
                "Re-run with gold labels after review.\n\n")


def _rel(path: Any) -> str:
    """Path relative to the repo when possible (label files may live elsewhere in tests)."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _banner(labels: str) -> str:
    return DRAFT_BANNER if labels == "drafts" else ""


def _missing(path: Any, labels: str) -> str:
    hint = "" if labels == "drafts" else " (or run with --labels drafts to exercise the harness on drafts)"
    return f"No labels found at {_rel(path)}{hint}."


# ---------------------------------------------------------------- E1 retrieval

def run_retrieval(args: argparse.Namespace) -> dict[str, Any]:
    from app.db import session_factory
    from app.models import Job
    from app.search.service import Mode, retrieve
    from app.search.text import build_profile_query_text, profile_keyword_query

    path = labels_path("retrieval_labels.jsonl", args.labels)
    labels = read_jsonl(path)
    if not labels:
        return {"skipped": _missing(path, args.labels)}
    profiles = load_profiles()
    by_profile: dict[str, dict[str, int]] = {}
    for lab in labels:
        by_profile.setdefault(lab["profile_id"], {})[lab["job_key"]] = int(lab["relevance"])
    methods = dict(METHODS)
    per: dict[str, dict[str, dict[str, float]]] = {}
    lat: dict[str, list[float]] = {}
    unjudged: dict[str, int] = {}
    with session_factory()() as s:
        keys = {j.id: f"{j.source}:{j.source_job_id}" for j in s.query(Job).all()}

        def run_method(mid: str, mode: str, model: str | None, variant: str | None) -> None:
            per[mid], lat[mid], unjudged[mid] = {}, [], 0
            for pid, judged in by_profile.items():
                if pid not in profiles:
                    continue
                prof = profiles[pid]
                t0 = time.perf_counter()
                hits = retrieve(s, build_profile_query_text(prof), profile_keyword_query(prof),
                                cast(Mode, mode), model, variant, limit=20)
                ids = [j for j, _ in hits][:20]
                lat[mid].append((time.perf_counter() - t0) * 1000)
                ranked = [keys.get(j, "") for j in ids]
                unjudged[mid] += sum(1 for k in ranked[:10] if k not in judged)
                rels = [judged.get(k, 0) for k in ranked]
                relevant = {k for k, r in judged.items() if r >= 1}
                per[mid][pid] = {
                    "p@5": M.precision_at_k(rels, 5), "ndcg@10": M.ndcg_at_k(rels, judged.values(), 10),
                    "mrr": M.mrr(rels, 2), "recall@20": M.recall_at_k(ranked, relevant, 20),
                }

        for mid in ("M1", "M2", "M3", "M4"):
            m = methods[mid]
            run_method(mid, m["mode"], m["model"], m["variant"])
        mean = {mid: {k: statistics.mean(v[k] for v in per[mid].values()) for k in ("p@5", "ndcg@10", "mrr", "recall@20")}
                for mid in per if per[mid]}
        best = max(("M2", "M3", "M4"), key=lambda x: mean.get(x, {}).get("ndcg@10", -1))
        methods["M5"] = {**methods["M5"], "model": methods[best]["model"], "variant": methods[best]["variant"],
                         "name": f"hybrid RRF (keyword + {methods[best]['name']})"}
        run_method("M5", "hybrid", methods[best]["model"], methods[best]["variant"])
    mean = {mid: {k: statistics.mean(v[k] for v in per[mid].values()) for k in ("p@5", "ndcg@10", "mrr", "recall@20")}
            for mid in per if per[mid]}
    n = len(next(iter(per.values()), {}))
    rows = [[mid, methods[mid]["name"], mean[mid]["p@5"], mean[mid]["ndcg@10"], mean[mid]["mrr"], mean[mid]["recall@20"],
             round(M.percentile(lat[mid], 50), 1), unjudged[mid]] for mid in per if mid in mean]
    md = (f"### E1 Retrieval (n = {n} profiles, {len(labels)} labels)\n\n" + _banner(args.labels)
          + md_table(["", "Method", "P@5", "nDCG@10", "MRR", "Recall@20", "p50 latency ms", "unjudged in top-10"], rows)
          + "\n\nM6 (LLM rerank) not run: optional, eval-only (spec 11.3).\n")
    return {"metrics": {"mean": mean, "per_profile": per, "best_vector": best, "n_profiles": n},
            "config": {"labels": args.labels, "label_file": str(_rel(path))}, "markdown": md}


# ---------------------------------------------------------------- E2 extraction

SCALARS = ["employment_type", "work_mode", "duration_months", "min_cgpa", "experience_min_years", "fresher_friendly",
           "apply_deadline", "ppo_or_conversion"]
LISTS = ["required_skills", "nice_to_have_skills", "degrees_allowed", "locations", "graduation_years", "specializations"]
FIELDS = SCALARS + ["pay"] + LISTS


def _empty(v: Any) -> bool:
    return v is None or v == [] or v == "" or (isinstance(v, dict) and all(x is None for x in v.values()))


def _scalar_eq(p: Any, g: Any) -> bool:
    if _empty(p) and _empty(g):
        return True
    if isinstance(p, int | float) and isinstance(g, int | float):
        return abs(float(p) - float(g)) < 1e-6
    return str(p).strip().lower() == str(g).strip().lower()


def _pay_eq(p: Any, g: Any) -> bool:
    if _empty(p) or _empty(g):
        return _empty(p) and _empty(g)

    def close(a: Any, b: Any) -> bool:
        if a is None or b is None:
            return a is None and b is None
        return abs(float(a) - float(b)) <= 0.01 * max(abs(float(b)), 1.0)

    return (close(p.get("min"), g.get("min")) and close(p.get("max"), g.get("max"))
            and (p.get("currency") or "").upper() == (g.get("currency") or "").upper()
            and p.get("period") == g.get("period"))


def _norm_gold(field: str, v: Any) -> Any:
    from app.extract.vocab import norm_degree, norm_location, norm_skill, norm_specialization

    if _empty(v) or not isinstance(v, list):
        return v
    fn = {"required_skills": norm_skill, "nice_to_have_skills": norm_skill, "degrees_allowed": norm_degree,
          "locations": norm_location, "specializations": norm_specialization}.get(field)
    return [fn(x) for x in v] if fn else v


def score_extraction(items: list[dict[str, Any]], preds: list[dict[str, Any]], dropped: list[int],
                     valid: list[bool]) -> dict[str, Any]:
    """Per-field accuracy (scalars exact, pay within 1%), list F1, grounded and hallucination rates."""
    acc: dict[str, list[float]] = {f: [] for f in SCALARS + ["pay"]}
    f1: dict[str, list[float]] = {f: [] for f in LISTS}
    halluc_num = halluc_den = kept = 0
    for it, pred in zip(items, preds, strict=True):
        gold = it["gold"]
        for f in FIELDS:
            g = _norm_gold(f, gold.get(f))
            p = (pred.get(f) or {}).get("value") if isinstance(pred.get(f), dict) else None
            if not _empty(p):
                kept += 1
            if _empty(g):
                halluc_den += 1
                halluc_num += 0 if _empty(p) else 1
            if f == "pay":
                acc[f].append(1.0 if _pay_eq(p, g) else 0.0)
            elif f in SCALARS:
                acc[f].append(1.0 if _scalar_eq(p, g) else 0.0)
            else:
                f1[f].append(M.set_prf([str(x).lower() for x in (p or [])], [str(x).lower() for x in (g or [])])[2])
    n = len(items)
    return {
        "n": n,
        "schema_valid_rate": sum(valid) / n if n else 0.0,
        "field_accuracy": {f: statistics.mean(v) for f, v in acc.items() if v},
        "list_f1": {f: statistics.mean(v) for f, v in f1.items() if v},
        "evidence_grounded_rate": kept / (kept + sum(dropped)) if kept + sum(dropped) else 1.0,
        "evidence_dropped": sum(dropped),
        "hallucination_rate": halluc_num / halluc_den if halluc_den else 0.0,
    }


def run_extraction(args: argparse.Namespace) -> dict[str, Any]:
    from app.extract.facts import extract_facts
    from app.llm.client import DailyLimitReached, get_llm, get_llm_for_model

    path = labels_path("extraction_gold.jsonl", args.labels)
    items = read_jsonl(path)
    if not items:
        return {"skipped": _missing(path, args.labels)}
    st = get_settings()
    prompts = (args.prompt or st.cfg.prompts.facts).split(",")
    models = (args.model or st.cfg.models.extract).split(",")
    runs: dict[str, Any] = {}
    rows = []
    for model in models:
        # each model on the provider that serves it (e.g. gpt-oss-20b on Groq, Gemini on Gemini)
        llm = get_llm("extract") if st.llm_provider == "fake" else get_llm_for_model(model)
        for pv in prompts:
            preds, dropped, valid = [], [], []
            try:
                for it in items:
                    title = it.get("title") or it["text"].split("\n", 1)[0].removeprefix("Title: ")
                    r = extract_facts(it["text"], title, it.get("company"), llm, pv, model=None)
                    preds.append(r.facts.model_dump(mode="json"))
                    dropped.append(len(r.dropped))
                    valid.append(r.valid)
            except DailyLimitReached as e:
                runs[f"{model}|{pv}"] = {"error": f"daily limit after {len(preds)} items: {e}"}
                continue
            m = score_extraction(items, preds, dropped, valid)
            used = llm.model if llm.model == "fake-heuristic" else model
            runs[f"{used}|{pv}"] = m
            fa = m["field_accuracy"]
            rows.append([used, pv, m["n"], m["schema_valid_rate"], statistics.mean(fa.values()),
                         statistics.mean(m["list_f1"].values()), fa.get("pay"), m["list_f1"].get("required_skills"),
                         m["evidence_grounded_rate"], m["hallucination_rate"]])
    md = (f"### E2 Extraction (n = {len(items)} jobs)\n\n" + _banner(args.labels)
          + md_table(["Model", "Prompt", "n", "Schema valid", "Scalar acc (mean)", "List F1 (mean)", "Pay acc",
                      "Req. skills F1", "Evidence grounded", "Hallucination"], rows)
          + "\n\nScalar acc: exact after normalization (null = null counts as correct). Pay: min/max within 1%, same "
            "currency and period. Evidence grounded: kept values / (kept + dropped for missing evidence). "
            "Hallucination: non-null prediction where gold is null.\n")
    return {"metrics": runs, "config": {"labels": args.labels, "prompts": prompts, "models": models}, "markdown": md}


def not_built(phase: str) -> Callable[[argparse.Namespace], dict[str, Any]]:
    def run(_: argparse.Namespace) -> dict[str, Any]:
        return {"skipped": f"suite not built yet (roadmap phase {phase})"}
    return run


SUITES: dict[str, Callable[[argparse.Namespace], dict[str, Any]]] = {
    "retrieval": run_retrieval,
    "extraction": run_extraction,
    "eligibility": not_built("P6"),
    "explanation": not_built("P6"),
    "agent": not_built("P6"),
    "trust": not_built("P5"),
}


def main() -> None:
    from app.logs import setup_logging

    setup_logging("WARNING")
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", required=True, choices=[*SUITES, "all"])
    ap.add_argument("--variants", default="all", help="retrieval: all (M1-M5)")
    ap.add_argument("--prompt", help="extraction: comma-separated prompt versions")
    ap.add_argument("--model", help="extraction: comma-separated model ids")
    ap.add_argument("--judge-model", help="explanation (P6)")
    ap.add_argument("--labels", choices=["gold", "drafts"], default="gold")
    ap.add_argument("--report", help="write all latest suite tables to this markdown file")
    args = ap.parse_args()
    for name in (SUITES if args.suite == "all" else [args.suite]):
        out = SUITES[name](args)
        if "skipped" in out:
            print(f"[{name}] skipped: {out['skipped']}")
            continue
        p = save_results(name, out["config"], out["metrics"], out["markdown"])
        print(out["markdown"])
        print(f"[{name}] saved {_rel(p)}")
    if args.report:
        parts = ["# JobLens evaluation results\n",
                 "Small samples: numbers are indicative. Each table states its sample size; see evals/results/*.json.\n"]
        for name in SUITES:
            md = RESULTS / f"{name}.md"
            if md.exists():
                parts.append(md.read_text(encoding="utf-8"))
        (ROOT / args.report).write_text("\n".join(parts), encoding="utf-8")
        print(f"report -> {args.report}")


if __name__ == "__main__":
    main()
