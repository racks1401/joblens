"""E1 retrieval eval: keyword vs vector vs hybrid (RRF) against reviewed labels.

    python -m evals.e1 [--pool retrieval_pool_v4.jsonl]

Two label sets, reported separately:
  (a) human-reviewed pairs only (evals/data/retrieval_labels_reviewed.jsonl);
  (b) every pool pair: the human label where one exists, otherwise the LLM draft ("includes LLM drafts").
Assumption: a retrieved job with no label for that profile counts as NOT relevant (relevance 0). The number of
such unlabeled jobs in each top-10 is reported so the effect is visible.

Metrics per profile, then the mean over profiles: P@5 and P@10 (relevance >= 1), nDCG@10 (gains 0/1/2,
ideal = best ordering of that profile's labels), MRR (rank of the first relevance-2 job).
"""

from __future__ import annotations

import argparse
import statistics
from collections.abc import Callable
from typing import Any

from app.config import get_settings
from evals import metrics as M
from evals.common import DATA, DRAFTS, load_profiles, md_table, read_jsonl, save_results

K = 10
ASSUMPTION = ("Pairs outside the labeled set count as not relevant (relevance 0); "
              "'unlabeled in top-10' shows how often that assumption is used.")


def methods() -> dict[str, dict[str, Any]]:
    e = get_settings().cfg.embeddings
    return {
        "keyword": {"mode": "keyword", "model": None, "variant": None},
        f"vector ({e.default_model}, {e.default_variant})": {"mode": "vector", "model": e.default_model,
                                                             "variant": e.default_variant},
        f"hybrid RRF (keyword + {e.default_model} {e.default_variant})": {"mode": "hybrid", "model": e.default_model,
                                                                         "variant": e.default_variant},
    }


def label_sets(pool_file: str) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, int]], dict[str, int]]:
    """(human-only labels, human-else-draft labels, count of human labels used in set b), keyed profile -> job_key."""
    human: dict[str, dict[str, int]] = {}
    for r in read_jsonl(DATA / "retrieval_labels_reviewed.jsonl"):
        human.setdefault(r["profile_id"], {})[r["job_key"]] = int(r["relevance"])
    drafts = {(d["profile_id"], d["job_key"]): int(d["relevance"]) for d in read_jsonl(DRAFTS / "retrieval_labels_draft.jsonl")}
    mixed: dict[str, dict[str, int]] = {}
    src = {"human": 0, "draft": 0}
    for r in read_jsonl(DATA / pool_file):
        key = (r["profile_id"], r["job_key"])
        if r["job_key"] in human.get(r["profile_id"], {}):
            mixed.setdefault(r["profile_id"], {})[r["job_key"]] = human[r["profile_id"]][r["job_key"]]
            src["human"] += 1
        elif key in drafts:
            mixed.setdefault(r["profile_id"], {})[r["job_key"]] = drafts[key]
            src["draft"] += 1
    return human, mixed, src


def evaluate(labels: dict[str, dict[str, int]], ranked: Callable[[str, str], list[str]],
             method_names: list[str]) -> dict[str, Any]:
    """ranked(profile_id, method) -> job_keys best first. Returns per-method mean + per-profile metrics."""
    out: dict[str, Any] = {}
    for m in method_names:
        per: dict[str, dict[str, float]] = {}
        unl = 0
        for pid, judged in labels.items():
            keys = ranked(pid, m)[:K]
            rels = [judged.get(k, 0) for k in keys]
            unl += sum(1 for k in keys if k not in judged)
            per[pid] = {"p@5": M.precision_at_k(rels, 5), "p@10": M.precision_at_k(rels, 10),
                        "ndcg@10": M.ndcg_at_k(rels, judged.values(), 10), "mrr": M.mrr(rels, 2),
                        "labels": len(judged), "unlabeled_in_top10": sum(1 for k in keys if k not in judged)}
        mean = {k: statistics.mean(v[k] for v in per.values()) for k in ("p@5", "p@10", "ndcg@10", "mrr")} if per else {}
        out[m] = {"mean": mean, "per_profile": per, "unlabeled_in_top10": unl, "profiles": len(per)}
    return out


def table(title: str, res: dict[str, Any], labels: dict[str, dict[str, int]], note: str) -> str:
    rows = [[m, r["mean"].get("p@5"), r["mean"].get("p@10"), r["mean"].get("ndcg@10"), r["mean"].get("mrr"),
             f"{r['unlabeled_in_top10']}/{K * r['profiles']}"] for m, r in res.items()]
    counts = ", ".join(f"{p}: {len(v)}" for p, v in sorted(labels.items()))
    return (f"#### {title}\n\n{note}\n\nLabeled pairs per profile: {counts} (total {sum(len(v) for v in labels.values())}).\n\n"
            + md_table(["Method", "P@5", "P@10", "nDCG@10", "MRR", "unlabeled in top-10"], rows) + "\n")


def fully_labeled(res: dict[str, Any], labels: dict[str, dict[str, int]]) -> str:
    """Per-method rows for the profile with the most human labels (p00), with its unlabeled count."""
    if not labels:
        return ""
    p = max(labels, key=lambda x: len(labels[x]))
    rows = [[m, r["per_profile"][p]["p@5"], r["per_profile"][p]["p@10"], r["per_profile"][p]["ndcg@10"],
             r["per_profile"][p]["mrr"], f"{r['per_profile'][p]['unlabeled_in_top10']}/{K}"] for m, r in res.items()]
    return (f"#### (a1) {p} only, human labels ({len(labels[p])} labeled pairs)\n\n"
            + md_table(["Method", "P@5", "P@10", "nDCG@10", "MRR", "unlabeled in top-10"], rows)
            + "\n\nOne profile, 10 results per method: a sanity check, not a deciding set.\n")


def main() -> None:
    from app.db import session_factory
    from app.models import Job
    from app.search.service import retrieve
    from app.search.text import build_profile_query_text, profile_keyword_query

    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default="retrieval_pool_v4.jsonl")
    args = ap.parse_args()
    human, mixed, src = label_sets(args.pool)
    profiles = load_profiles()
    ms = methods()
    cache: dict[tuple[str, str], list[str]] = {}
    with session_factory()() as s:
        keys = {j.id: f"{j.source}:{j.source_job_id}" for j in s.query(Job).all()}

        def ranked(pid: str, m: str) -> list[str]:
            if (pid, m) not in cache:
                p, c = profiles[pid], ms[m]
                # Same candidate depth as production /matches (search.candidates per list), then cut to K.
                hits = retrieve(s, build_profile_query_text(p), profile_keyword_query(p), c["mode"], c["model"],
                                c["variant"], limit=get_settings().cfg.search.candidates)
                cache[(pid, m)] = [keys[j] for j, _ in hits][:K]
            return cache[(pid, m)]

        res_h = evaluate(human, ranked, list(ms))
        res_m = evaluate(mixed, ranked, list(ms))
    md = "\n".join([
        "### E1 Retrieval (indicative only)\n",
        "**Small sample, noisy numbers.** One reviewer; 5 profiles; at most ~25 labels per profile. Differences of a "
        "few points between methods are not meaningful.\n", ASSUMPTION + "\n",
        table("(a) Human-reviewed labels only", res_h, human, "Only pairs a human reviewed (status human_reviewed)."),
        fully_labeled(res_h, human),
        table("(b) All pool pairs — includes LLM drafts", res_m, mixed,
              f"Human label where it exists ({src['human']} pairs), otherwise the qwen draft ({src['draft']} pairs, "
              "status draft, NOT hand-labeled)."),
    ])
    save_results("retrieval_e1", {"pool": args.pool, "k": K, "methods": ms, "assumption": ASSUMPTION,
                                  "label_sources_b": src},
                 {"human_only": res_h, "includes_llm_drafts": res_m}, md)
    print(md)


if __name__ == "__main__":
    main()
