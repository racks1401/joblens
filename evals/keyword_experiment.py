"""Experiment (not production): keyword query variants for profile matching.

    python -m evals.keyword_experiment

current     spec 11.3: target roles + top-10 skills joined with OR (websearch_to_tsquery), ranked by ts_rank_cd.
role_boost  a job must match at least one target-role word; rank = ts_rank_cd(roles) + ts_rank_cd(skills), so
            skills only boost the ranking among role matches (weight 1, not tuned).
Each variant is evaluated as keyword alone and fused with the same vector list (RRF, production depth).
Deciding set: all pool pairs with drafts (134 pairs, 5 profiles); p00 human-only is a sanity check.
"""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.search.hybrid import rrf
from app.search.keyword import keyword_search
from app.search.text import build_profile_query_text, profile_keyword_query
from app.search.vector import vector_search
from evals.common import RESULTS, load_profiles, md_table
from evals.e1 import K, evaluate, label_sets

ROLE_BOOST_SQL = text("""
SELECT id, ts_rank_cd(fts, rq) + ts_rank_cd(fts, sq) AS score
FROM jobs, websearch_to_tsquery('english', :roles) AS rq, websearch_to_tsquery('english', :skills) AS sq
WHERE is_active AND in_scope AND fts @@ rq
ORDER BY score DESC, id
LIMIT :limit
""")
STOP = {"and", "or", "the", "of", "for", "a", "an", "&"}


def role_words(roles: list[str]) -> str:
    words = list(dict.fromkeys(w for r in roles for w in re.findall(r"[a-z0-9+#.]+", r.lower()) if w not in STOP))
    return " OR ".join(words)


def role_boost_search(s: Session, p: Any, limit: int) -> list[int]:
    roles = role_words(p.target_roles)
    skills = " OR ".join(x.name for x in p.skills[:10])
    if not roles:
        return []
    return [int(r.id) for r in s.execute(ROLE_BOOST_SQL, {"roles": roles, "skills": skills or roles, "limit": limit})]


def main() -> None:
    from app.db import session_factory
    from app.models import Job
    from app.search.embed import get_embedder

    st = get_settings().cfg
    depth, model, variant = st.search.candidates, st.embeddings.default_model, st.embeddings.default_variant
    human, mixed, _ = label_sets("retrieval_pool_v4.jsonl")
    profiles = load_profiles()
    lists: dict[tuple[str, str], list[str]] = {}
    with session_factory()() as s:
        key = {j.id: f"{j.source}:{j.source_job_id}" for j in s.query(Job).all()}
        for pid in mixed:
            p = profiles[pid]
            vec = [j for j, _ in vector_search(s, get_embedder(model).embed_query(build_profile_query_text(p)), model,
                                               variant, depth)]
            kw_cur = [j for j, _ in keyword_search(s, profile_keyword_query(p), depth)]
            kw_new = role_boost_search(s, p, depth)
            for name, ids in (("keyword / current", kw_cur), ("keyword / role_boost", kw_new),
                              ("hybrid / current", [j for j, _ in rrf([kw_cur, vec], k=st.search.rrf_k, top=depth)]),
                              ("hybrid / role_boost", [j for j, _ in rrf([kw_new, vec], k=st.search.rrf_k, top=depth)])):
                lists[(pid, name)] = [key[j] for j in ids][:K]
    names = ["keyword / current", "keyword / role_boost", "hybrid / current", "hybrid / role_boost"]
    res_m = evaluate(mixed, lambda pid, m: lists[(pid, m)], names)
    res_p00 = evaluate({"p00": human["p00"]}, lambda pid, m: lists[(pid, m)], names)
    per_profile = {n: {p: round(v["ndcg@10"], 3) for p, v in res_m[n]["per_profile"].items()} for n in names}

    def rows(res: dict[str, Any]) -> list[list[Any]]:
        return [[n, r["mean"]["ndcg@10"], r["mean"]["mrr"], r["mean"]["p@5"], f"{r['unlabeled_in_top10']}/{K * r['profiles']}"]
                for n, r in res.items()]

    md = "\n".join([
        "### Keyword query experiment (not production)\n",
        "Assumption: unlabeled retrieved jobs count as not relevant. Small, noisy sample.\n",
        "#### Deciding set: all pool pairs, includes LLM drafts (134 pairs: 49 human + 85 qwen drafts)\n",
        md_table(["Variant", "nDCG@10", "MRR", "P@5", "unlabeled in top-10"], rows(res_m)),
        "\nnDCG@10 per profile: " + "; ".join(f"{n}: {per_profile[n]}" for n in names),
        "\n#### Sanity check: p00 human labels only (24 pairs)\n",
        md_table(["Variant", "nDCG@10", "MRR", "P@5", "unlabeled in top-10"], rows(res_p00)), "\n"])
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "keyword_experiment.json").write_text(json.dumps({"with_drafts": res_m, "p00_human": res_p00}, indent=1),
                                                    encoding="utf-8")
    (RESULTS / "keyword_experiment.md").write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
