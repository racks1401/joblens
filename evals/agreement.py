"""Agreement between human-reviewed retrieval labels and the LLM drafts shown at review time.

    python -m evals.agreement [--main-profile p00]

Writes evals/results/retrieval_agreement.json and .md. Counts are exact; the sample is small.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from typing import Any

from evals.common import DATA, RESULTS, md_table, read_jsonl


def job_titles() -> dict[str, tuple[str, str]]:
    """job_key -> (title, company) from the pool files (no DB needed)."""
    out: dict[str, tuple[str, str]] = {}
    for f in sorted(DATA.glob("retrieval_pool*.jsonl")):
        for r in read_jsonl(f):
            out[r["job_key"]] = (r["title"], r["company"])
    return out


def agreement(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    same = sum(r["relevance"] == r["draft_relevance"] for r in rows)
    within1 = sum(abs(r["relevance"] - r["draft_relevance"]) <= 1 for r in rows)
    cm = Counter((r["draft_relevance"], r["relevance"]) for r in rows)
    return {"n": n, "kept": same, "changed": n - same, "agreement": same / n if n else None,
            "within_one": within1, "confusion_draft_to_human": {f"{a}->{b}": c for (a, b), c in sorted(cm.items())}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--main-profile", default="p00")
    args = ap.parse_args()
    rows = read_jsonl(DATA / "retrieval_labels_reviewed.jsonl")
    titles = job_titles()
    groups = {f"{args.main_profile}": [r for r in rows if r["profile_id"] == args.main_profile],
              "sample (other profiles)": [r for r in rows if r["profile_id"] != args.main_profile],
              "all": rows}
    res = {k: agreement(v) for k, v in groups.items()}
    changed = [r for r in rows if r["relevance"] != r["draft_relevance"]]
    md = ["### Draft vs human agreement (retrieval labels)\n",
          "Exact agreement = human label equal to the LLM draft shown at review time. Small sample.\n",
          md_table(["Group", "Pairs", "Kept draft", "Changed", "Agreement", "Within ±1"],
                   [[k, v["n"], v["kept"], v["changed"], f"{v['kept']}/{v['n']} ({v['agreement']:.0%})" if v["n"] else "—",
                     f"{v['within_one']}/{v['n']}"] for k, v in res.items()]),
          "\nDraft → human transitions (all): " + ", ".join(f"{k}: {v}" for k, v in res["all"]["confusion_draft_to_human"].items()),
          "\n\n#### Changed pairs\n",
          md_table(["Job", "Company", "Profile", "Draft → Human", "Draft reason", "Human reason"],
                   [[titles.get(r["job_key"], ("?", "?"))[0][:55], titles.get(r["job_key"], ("?", "?"))[1],
                     r["profile_id"], f"{r['draft_relevance']} → {r['relevance']}", r["draft_reason"][:70],
                     r["reason"] if not r["reason"].startswith("human: changed") else "(not recorded)"] for r in changed]),
          "\n"]
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "retrieval_agreement.json").write_text(json.dumps({"groups": res, "changed": changed}, indent=1), encoding="utf-8")
    (RESULTS / "retrieval_agreement.md").write_text("\n".join(md), encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
