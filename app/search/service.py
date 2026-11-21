"""Search (keyword | vector | hybrid) and profile matching (spec 4.1 match flow, 11.3, 11.4)."""

from __future__ import annotations

import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.eligibility import check_eligibility, monthly_inr, pay_view, usable_facts
from app.models import Job, JobFactsRow, TrustScore
from app.schemas import EligibilityResult, JobFacts, Profile, TrustResult
from app.search.embed import get_embedder, serving_model
from app.search.hybrid import rrf
from app.search.keyword import keyword_search
from app.search.rank import final_score, skill_overlap
from app.search.text import build_profile_query_text, profile_keyword_query
from app.search.vector import vector_search

Mode = Literal["keyword", "vector", "hybrid"]


class Filters(BaseModel):
    """Optional post-retrieval filters (used by /search and the agent's search_jobs tool)."""

    employment_type: str | None = None
    work_mode: str | None = None
    location: str | None = None
    min_monthly_pay_inr: int | None = None
    fresher_only: bool = False
    eligible_only: bool = False  # excludes INELIGIBLE (needs a profile)
    exclude_companies: list[str] = []
    exclude_locations: list[str] = []

    def active(self) -> bool:
        return any([self.employment_type, self.work_mode, self.location, self.min_monthly_pay_inr,
                    self.fresher_only, self.eligible_only, self.exclude_companies, self.exclude_locations])


class MatchItem(BaseModel):
    job_id: int
    title: str
    company: str
    url: str | None
    location_raw: str | None
    final_score: int
    retrieval_score: float
    skill_overlap: float | None
    matched_skills: list[str] = []
    eligibility: EligibilityResult | None = None
    trust: TrustResult | None = None
    flagged: bool = False
    # stored facts, shown as-is (null = the posting, as extracted, does not state it)
    work_mode: str | None = None
    employment_type: str | None = None
    pay: dict[str, Any] | None = None
    apply_deadline: str | None = None
    expired: bool = False


@dataclass
class Loaded:
    job: Job
    facts: JobFacts | None  # None: no usable extracted facts (not extracted yet, or extraction failed)
    trust: TrustResult | None


def load_jobs(s: Session, ids: Sequence[int]) -> dict[int, Loaded]:
    """Bulk-load jobs with facts and trust."""
    if not ids:
        return {}
    rows = s.execute(
        select(Job, JobFactsRow.facts, JobFactsRow.valid, TrustScore)
        .outerjoin(JobFactsRow, JobFactsRow.job_id == Job.id)
        .outerjoin(TrustScore, TrustScore.job_id == Job.id)
        .where(Job.id.in_(list(ids)))
    ).all()
    out = {}
    for job, facts, valid, tr in rows:
        f = usable_facts(facts, valid)
        t = TrustResult(score=tr.score, level=tr.level, reasons=tr.reasons, scam_prob=tr.scam_prob) if tr else None  # type: ignore[arg-type]
        out[job.id] = Loaded(job, f, t)
    return out


def retrieve(s: Session, query_text: str, keyword_query: str, mode: Mode, model: str | None = None,
             variant: str | None = None, limit: int | None = None,
             include_out_of_scope: bool = False) -> list[tuple[int, float]]:
    """Candidate ids with a 0..1 retrieval score. Keyword scores are divided by the max ts_rank_cd."""
    st = get_settings().cfg
    model = serving_model(model)
    variant = variant or st.embeddings.default_variant
    n = limit or st.search.candidates
    kw: list[tuple[int, float]] = []
    vec: list[tuple[int, float]] = []
    if mode in ("keyword", "hybrid"):
        kw = keyword_search(s, keyword_query, n, include_out_of_scope)
    if mode in ("vector", "hybrid"):
        vec = vector_search(s, get_embedder(model).embed_query(query_text), model, variant, n, include_out_of_scope)
    if mode == "keyword":
        mx = max((x[1] for x in kw), default=1.0) or 1.0
        return [(j, sc / mx) for j, sc in kw]
    if mode == "vector":
        return vec
    return rrf([[j for j, _ in kw], [j for j, _ in vec]], k=st.search.rrf_k, top=n)


def _loc_hit(needle: str, job: Job, f: JobFacts) -> bool:
    n = needle.strip().lower()
    hay = " ".join([job.location_raw or "", *(f.locations.value or [])]).lower()
    return bool(n) and n in hay


def passes(loaded: Loaded, flt: Filters, elig: EligibilityResult | None) -> bool:
    f, job = loaded.facts, loaded.job
    if f is None:
        return False
    if flt.employment_type and f.employment_type.value != flt.employment_type:
        return False
    if flt.work_mode and f.work_mode.value != flt.work_mode:
        return False
    if flt.location and not (_loc_hit(flt.location, job, f) or (flt.location.lower() == "remote" and f.work_mode.value == "remote")):
        return False
    if flt.min_monthly_pay_inr:
        inr = monthly_inr(f.pay.value, f.duration_months.value) if f.pay.value else None
        if inr is None or inr < flt.min_monthly_pay_inr:
            return False
    if flt.fresher_only and not (f.fresher_friendly.value or (f.experience_min_years.value is not None
                                                              and f.experience_min_years.value <= 1)):
        return False
    if flt.eligible_only and elig is not None and elig.verdict == "INELIGIBLE":
        return False
    if any(c.strip().lower() == job.company.lower() for c in flt.exclude_companies):
        return False
    locs = [x.lower() for x in (f.locations.value or [])]
    if flt.exclude_locations and locs and all(any(e.lower() in x for e in flt.exclude_locations) for x in locs):
        return False
    return True


def _item(loaded: Loaded, retrieval: float, profile: Profile | None, show_ineligible: bool,
          flt: Filters | None, include_expired: bool = False) -> MatchItem | None:
    cfg = get_settings().cfg.ranking
    if loaded.job.expired and not include_expired:
        return None
    f = loaded.facts
    if f is None:  # hidden until its requirements are extracted (retrieval already skips these; this is a backstop)
        return None
    elig = check_eligibility(profile, f) if profile else None
    if elig and elig.verdict == "INELIGIBLE" and not show_ineligible:
        return None
    if flt and not passes(loaded, flt, elig):
        return None
    pskills = [x.name for x in profile.skills] if profile else []
    ov = skill_overlap(pskills, f.required_skills.value, f.nice_to_have_skills.value) if profile else None
    level = loaded.trust.level if loaded.trust else None
    score = final_score(retrieval, ov, elig.verdict if elig else "ELIGIBLE", level, cfg)
    job_sk = set((f.required_skills.value or []) + (f.nice_to_have_skills.value or []))
    return MatchItem(
        job_id=loaded.job.id, title=loaded.job.title, company=loaded.job.company, url=loaded.job.url,
        location_raw=loaded.job.location_raw, final_score=score, retrieval_score=round(retrieval, 4),
        skill_overlap=None if ov is None else round(ov, 4), matched_skills=sorted(set(pskills) & job_sk),
        eligibility=elig, trust=loaded.trust, flagged=level == "risky",
        work_mode=f.work_mode.value, employment_type=f.employment_type.value, pay=pay_view(f),
        apply_deadline=f.apply_deadline.value.isoformat() if f.apply_deadline.value else None,
        expired=loaded.job.expired,
    )


def match(s: Session, profile: Profile, limit: int = 20, show_ineligible: bool = False,
          filters: Filters | None = None, mode: Mode = "hybrid", include_out_of_scope: bool = False,
          include_expired: bool = False) -> list[MatchItem]:
    """Ranked matches for a profile. Expired jobs (deadline passed) are left out unless include_expired."""
    n = get_settings().cfg.search.candidates * (4 if filters and filters.active() else 1)
    cands = retrieve(s, build_profile_query_text(profile), profile_keyword_query(profile), mode, limit=n,
                     include_out_of_scope=include_out_of_scope)
    loaded = load_jobs(s, [j for j, _ in cands])
    items = [it for j, sc in cands if j in loaded
             if (it := _item(loaded[j], sc, profile, show_ineligible, filters, include_expired)) is not None]
    items.sort(key=lambda x: (-x.final_score, -x.retrieval_score, x.job_id))
    return items[:limit]


def search(s: Session, q: str, mode: Mode = "hybrid", model: str | None = None, variant: str | None = None,
           limit: int = 20, filters: Filters | None = None, profile: Profile | None = None,
           include_out_of_scope: bool = False, include_expired: bool = False) -> dict[str, Any]:
    """Free-text search. With a profile, results carry eligibility and use the final score. Expired jobs are
    left out unless include_expired."""
    t0 = time.perf_counter()
    n = get_settings().cfg.search.candidates * (4 if filters and filters.active() else 1)
    kwq = " OR ".join(x for x in re.split(r"\s*,\s*", q) if x) if "," in q else q
    cands = retrieve(s, q, kwq, mode, model, variant, limit=n, include_out_of_scope=include_out_of_scope)
    loaded = load_jobs(s, [j for j, _ in cands])
    items = [it for j, sc in cands if j in loaded
             if (it := _item(loaded[j], sc, profile, True, filters, include_expired)) is not None]
    if profile:
        items.sort(key=lambda x: (-x.final_score, -x.retrieval_score, x.job_id))
    return {"query": q, "mode": mode, "results": items[:limit], "latency_ms": round((time.perf_counter() - t0) * 1000, 1)}
