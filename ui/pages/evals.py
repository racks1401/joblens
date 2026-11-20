"""Evals: the committed evaluation numbers (GET /evals/summary), each with its sample size and caveat."""

from __future__ import annotations

import streamlit as st
from lib import APIError, api, show_error

st.title("Evals")
st.caption("Numbers from the committed files in evals/results. Every number has its sample size next to it; the "
           "samples are small, so read them as indicative.")
try:
    s = api("GET", "/evals/summary")
except APIError as e:
    show_error(e)
    st.stop()

# --- E1 retrieval
st.subheader("Retrieval (E1)")
e1 = s.get("retrieval_e1")
if e1:
    st.warning(e1["caveat"], icon=":material/info:")
    names = {"human_only": "Human-reviewed labels only", "includes_llm_drafts": "All labels, including LLM drafts"}
    for g, methods in e1["groups"].items():
        any_m = next(iter(methods.values()))
        extra = ""
        if g == "includes_llm_drafts" and e1.get("label_sources_with_drafts"):
            src = e1["label_sources_with_drafts"]
            extra = f" ({src.get('human')} human-reviewed + {src.get('draft')} LLM drafts, not hand-labeled)"
        st.markdown(f"**{names.get(g, g)}**: n = {any_m['labels']} labeled pairs over {any_m['profiles']} profiles"
                    f"{extra}, top {e1['k']} results per profile.")
        rows = [{"Method": m, "P@5": round(v["mean"]["p@5"], 3), "P@10": round(v["mean"]["p@10"], 3),
                 "nDCG@10": round(v["mean"]["ndcg@10"], 3), "MRR": round(v["mean"]["mrr"], 3),
                 "Unlabeled in top-10": f"{v['unlabeled_in_top10']}/{v['profiles'] * e1['k']}"}
                for m, v in methods.items()]
        st.dataframe(rows, hide_index=True, use_container_width=True)
    st.caption(e1["assumption"])
else:
    st.info("No E1 results file.")

# --- trust classifier
st.subheader("Trust: scam text classifier")
t = s.get("trust_classifier")
if t:
    st.warning(t["data"]["caveat"] + " Only rule checks can mark a job as having scam signals; the classifier only "
               "lowers the score.", icon=":material/info:")
    m = t["test_at_val_threshold"]
    sp = t["split"]
    st.markdown(f"Held-out test split: **n = {sp['test']:,} postings ({sp['test_fraud']} fraudulent)**; trained on "
                f"{sp['train']:,}, threshold {m['threshold']} chosen on {sp['val']:,} validation postings. Training "
                f"data: {t['data']['rows']:,} Kaggle EMSCAD postings, {t['data']['share_US']:.0%} US, "
                f"{t['data']['share_GB']:.0%} GB, {t['data']['share_IN']:.1%} India.")
    c = st.columns(5)
    c[0].metric("Precision", f"{m['precision']:.3f}", help=f"{m['tp']} true / {m['tp'] + m['fp']} flagged")
    c[1].metric("Recall", f"{m['recall']:.3f}", help=f"{m['tp']} of {m['tp'] + m['fn']} fraudulent")
    c[2].metric("F1", f"{m['f1']:.3f}")
    c[3].metric("PR-AUC", f"{t['test_pr_auc']:.3f}")
    c[4].metric("Recall at precision ≥ 0.8", f"{t['test_recall_at_precision_0.8']:.3f}")
    st.caption(f"All on n = {sp['test']:,} test postings from the same 2012-2014 dataset, not on JobLens jobs.")

# --- agent
st.subheader("Agent test conversations")
st.warning(s["agent_caveat"], icon=":material/info:")
rows = [{"Run": r["run"].replace("agent_", ""), "Conversations (n)": r["conversations"], "Turns": r["turns"],
         "Grounding check passed": f"{r['grounded']}/{r['conversations']}",
         "Strict (tool outputs only)": f"{r['grounded_tools_only']}/{r['conversations']}",
         "Manual review": r["manual_review"] or "no review file", "Tokens": r["total_tokens"]}
        for r in s["agent_runs"]]
st.dataframe(rows, hide_index=True, use_container_width=True)
st.caption("The agent's answers were checked against raw tool outputs in a manual review (files in "
           "evals/results/agent_run*/manual_review.md), and the mistakes that remain are listed in Known "
           "limitations. Run 5 re-ran only the 4 conversations judged wrong or not answered in run 4.")

# --- label agreement
st.subheader("Label agreement: LLM drafts vs human review")
a = s.get("label_agreement")
if a:
    rows = [{"Group": g, "Pairs (n)": v["n"], "Draft kept": v["kept"], "Changed": v["changed"],
             "Exact agreement": f"{v['agreement']:.1%}", "Within one grade": f"{v['within_one']}/{v['n']}"}
            for g, v in a["groups"].items()]
    st.dataframe(rows, hide_index=True, use_container_width=True)
    st.caption("The builder reviewed the 49 labels (one reviewer); 0-2 relevance grades. High agreement on 49 pairs does not make the 85 unreviewed "
               "drafts reliable.")
