"""Rule-based fresher relevance (no LLM). JobLens targets interns, new grads and 0-2 year roles.

Not fresher-relevant if:
- the title has a seniority word: senior, sr, staff, principal, lead, manager, director, head, architect
  (also vp / vice president / chief), or a numeric level >= 3 (Engineer 3, SDE - 3, III, IV, L4+), or
- the title or posting asks for X+ years with X >= 3, taking the FIRST requirement stated (not the
  smallest number), including phrasings without the word "experience" ("5+ years in platform engineering",
  "Have 3-5 years building ..."), or
- after extraction, the evidenced JobFacts.experience_min_years is >= 3 (see apply_extracted_experience).
A clearly junior title (intern, graduate, new grad, trainee, ...) overrides the years rules and the soft
title words (lead/manager/head/architect).

Rows are kept in the DB with is_fresher_relevant=false; matching, evals and extraction skip them by default.

    python -m app.ingest.seniority --backfill    # re-apply all rules (+ India eligibility) to every stored job
"""

from __future__ import annotations

import re
from typing import Any

SENIOR_TITLE = re.compile(
    r"\b(senior|sr|snr|staff|principal|lead|leader|manager|director|head|architect|vp|vice president|chief|"
    r"distinguished|fellow)\b\.?|\b(III|IV|V)\b|\b(SDE|SWE|MTS)[\s-]?(3|4|5)\b", re.I)
# Numeric level >= 3 after a role word: "Software Engineer 3", "SDE - 3", "Analyst 4", "L5", "Level 4".
LEVEL_TITLE = re.compile(
    r"\b(?:engineer|developer|sde|swe|mts|scientist|analyst|consultant)\s*[-–]?\s*(?:[3-9]|iii|iv|v)\b|"
    r"\bL[4-9]\b|\blevel\s*[3-9]\b", re.I)
JUNIOR_TITLE = re.compile(
    r"\b(intern|internship|graduate|grad|new grad|fresher|trainee|entry[- ]level|junior|jr|apprentice|"
    r"campus|early career|university)\b", re.I)

_N = r"(\d{1,2})\s*(?:\+|plus)?\s*(?:(?:-|–|to)\s*\d{1,2}\s*\+?\s*)?(?:years?|yrs?)(?:'|’)?"
YEARS = [
    # "3+ years of experience", "5 years' relevant experience"
    re.compile(r"\b" + _N + r"\b[^.\n]{0,60}?\bexperience", re.I),
    # "experience: 4-8 yrs", "experience of 3+ years"
    re.compile(r"\bexp(?:erience)?\b[^.\n]{0,40}?\b" + _N + r"\b", re.I),
    # "minimum 4 years", "at least 3 years", "minimum 8-year"
    re.compile(r"\b(?:minimum|min\.?|at least)\s+(?:of\s+)?(\d{1,2})[\s-]*\+?\s*(?:years?|yrs?)\b", re.I),
    # requirement bullets without "experience": "- 5+ years in ...", "• 3–6 years building ..."
    re.compile(r"(?:^|\n)\s*(?:[-*•]\s*)?(?:\(required\)\s*)?" + _N +
               r"\s+(?:\([^)\n]{0,30}\)\s+)?(?:in|of|building|working|running|writing|developing|designing|across|as|with|leading|"
               r"shipping|operating|authoring|doing|owning)\b", re.I),
    # "Have 5+ years", "ideally with 5+ years", "bring 4+ years", "requires 3 years"
    re.compile(r"\b(?:have|has|bring|with|requires?|need|needs)\s+(?:at least\s+)?" + _N + r"\b", re.I),
]
MIN_SENIOR_YEARS = 3


def min_years_required(text: str) -> int | None:
    """The FIRST 'X years' requirement stated in the text (None if none).

    First, not smallest: "5+ years ..., with at least 2 years in X" is a 5-year role.
    """
    found: list[tuple[int, int]] = []
    for rx in YEARS:
        for m in rx.finditer(text or ""):
            v = int(m.group(1))
            if v <= 30:
                found.append((m.start(1), v))
    return min(found)[1] if found else None


# A junior title overrides these weaker words ("Product Manager Intern", "Associate Product Manager",
# "Graduate Solutions Architect"), but never senior/staff/principal/director/vp/chief.
SOFT_SENIOR = {"lead", "leader", "manager", "head", "architect"}
APM = re.compile(r"\bassociate product manager\b|\bAPM\b", re.I)


def is_junior_title(title: str) -> bool:
    return bool(JUNIOR_TITLE.search(title or "") or APM.search(title or ""))


def classify(title: str, description: str) -> tuple[bool, str]:
    """(is_fresher_relevant, reason) from title + posting text."""
    t = title or ""
    junior = is_junior_title(t)
    m = SENIOR_TITLE.search(t)
    word = (m.group(0).strip(" .") if m else "").lower()
    if m and not (junior and word in SOFT_SENIOR):
        return False, f"title has '{word}'"
    lv = LEVEL_TITLE.search(t)
    if lv and not junior:
        return False, f"title level '{lv.group(0)}'"
    if junior:
        return True, "junior title"
    ty = min_years_required(t)
    if ty is not None and ty >= MIN_SENIOR_YEARS:
        return False, f"title asks for {ty}+ years"
    yrs = min_years_required(description)
    if yrs is not None and yrs >= MIN_SENIOR_YEARS:
        return False, f"asks for {yrs}+ years of experience"
    return True, "no seniority signal" if yrs is None else f"asks for {yrs} years"


def apply_extracted_experience(job: Any, facts: dict[str, Any] | None) -> None:
    """After extraction: an evidenced experience_min_years >= 3 marks the job not fresher-relevant
    (junior titles excepted). Only ever turns relevance off; never on."""
    ex = (facts or {}).get("experience_min_years") or {}
    v, ev = ex.get("value"), ex.get("evidence")
    if job.is_fresher_relevant and v is not None and ev and float(v) >= MIN_SENIOR_YEARS and not is_junior_title(job.title):
        job.is_fresher_relevant = False
        job.seniority_reason = f"extracted: asks for {float(v):g}+ years (\"{str(ev)[:80]}\")"


def backfill() -> dict[str, int]:
    from sqlalchemy import select

    from app.db import session_factory
    from app.ingest.store import _eligibility
    from app.models import Job, JobFactsRow

    out = {"relevant": 0, "not_relevant": 0, "india_eligible": 0, "not_india_eligible": 0}
    with session_factory()() as s:
        for job, facts in s.execute(select(Job, JobFactsRow.facts).outerjoin(JobFactsRow, JobFactsRow.job_id == Job.id)):
            ok, reason = classify(job.title, job.description_text)
            job.is_fresher_relevant, job.seniority_reason = ok, reason
            apply_extracted_experience(job, facts)
            out["relevant" if job.is_fresher_relevant else "not_relevant"] += 1
            el, why = _eligibility(job.location_raw)
            job.is_india_eligible, job.location_reason = el, why
            out["india_eligible" if el else "not_india_eligible"] += 1
        s.commit()
    return out


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true")
    if ap.parse_args().backfill:
        print(backfill())
