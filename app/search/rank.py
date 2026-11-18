"""Skill overlap and final score (spec 11.4)."""

from __future__ import annotations

from collections.abc import Iterable

from app.config import RankingCfg


def skill_overlap(profile_skills: Iterable[str], required: Iterable[str] | None,
                  nice: Iterable[str] | None) -> float | None:
    """Weighted overlap: required match 1.0, nice-to-have 0.5, divided by (n_req + 0.5 n_nice), capped at 1.

    Returns None when the job lists no skills.
    """
    ps = {s.lower() for s in profile_skills}
    req = {s.lower() for s in (required or [])}
    nic = {s.lower() for s in (nice or [])} - req
    denom = len(req) + 0.5 * len(nic)
    if denom == 0:
        return None
    num = len(ps & req) + 0.5 * len(ps & nic)
    return min(1.0, num / denom)


def final_score(retrieval_norm: float, overlap: float | None, verdict: str, trust_level: str | None,
                cfg: RankingCfg) -> int:
    """0..100. CHECK: -check_penalty; risky: x risky_factor; caution: x caution_factor.

    If overlap is None its weight moves to retrieval. INELIGIBLE filtering is done by the caller.
    """
    base = retrieval_norm if overlap is None else cfg.w_retrieval * retrieval_norm + cfg.w_skill * overlap
    if verdict == "CHECK":
        base -= cfg.check_penalty
    if trust_level == "risky":
        base *= cfg.risky_factor
    elif trust_level == "caution":
        base *= cfg.caution_factor
    return round(100 * min(1.0, max(0.0, base)))
