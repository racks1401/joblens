"""Trust score (spec 13): start at 100, apply rule / ghost / classifier deltas, clamp 0..100, map to a level.

Level: >= levels.ok -> ok, >= levels.caution -> caution, else risky. Every delta carries a reason. Only rule
reasons (money, offplatform, free_email, unrealistic_pay, missing_company) describe scam signals; the
classifier reason reports a probability from a model not validated on Indian/ATS postings.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Job, JobFactsRow, TrustScore
from app.schemas import JobFacts, TrustReason, TrustResult
from app.trust.classifier import scam_probability
from app.trust.ghost import run_ghost
from app.trust.rules import run_rules

RULE_CODES = {"money", "offplatform", "free_email", "unrealistic_pay", "missing_company"}


def level_for(score: int, levels: dict[str, int]) -> str:
    return "ok" if score >= levels["ok"] else ("caution" if score >= levels["caution"] else "risky")


def compute_trust(job: Any, facts: JobFacts | None, now: datetime | None = None, model: Any | None = None,
                  use_classifier: bool = True) -> TrustResult:
    cfg = get_settings().cfg.trust
    text = job.description_text or ""
    reasons: list[TrustReason] = run_ghost(job, cfg, now) + run_rules(text, job.company or "", job.url, facts, cfg)
    prob = scam_probability(f"{job.title}\n{text}", model) if use_classifier else None
    if prob is not None:
        d = -round(abs(cfg.deltas["classifier_max"]) * prob)
        if d:
            reasons.append(TrustReason(code="classifier", message=f"Text classifier scam probability {prob:.2f} "
                                       "(model not validated on Indian/ATS postings)", delta=d))
    score = max(0, min(100, 100 + sum(r.delta for r in reasons)))
    return TrustResult(score=score, level=level_for(score, cfg.levels), reasons=reasons, scam_prob=prob)  # type: ignore[arg-type]


def store_trust(s: Session, job_id: int, t: TrustResult) -> None:
    vals = {"score": t.score, "level": t.level, "reasons": [r.model_dump() for r in t.reasons], "scam_prob": t.scam_prob,
            "computed_at": datetime.now(UTC)}
    s.execute(insert(TrustScore).values(job_id=job_id, **vals).on_conflict_do_update(index_elements=["job_id"], set_=vals))


def process_jobs(s: Session, job_ids: Sequence[int], **_: object) -> dict[str, int]:
    """Pipeline step: compute and store trust for the given jobs."""
    out = {"ok": 0, "caution": 0, "risky": 0}
    for jid in job_ids:
        job = s.get(Job, jid)
        if job is None:
            continue
        fr = s.get(JobFactsRow, jid)
        t = compute_trust(job, JobFacts.model_validate(fr.facts) if fr else None)
        store_trust(s, jid, t)
        out[t.level] += 1
    s.commit()
    return out


def scam_rules_fired(t: TrustResult) -> list[TrustReason]:
    """The rule reasons only: the sole basis for saying a posting looks like a scam."""
    return [r for r in t.reasons if r.code in RULE_CODES and r.code != "missing_company"]
