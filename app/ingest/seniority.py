"""Rule-based fresher relevance (no LLM). JobLens targets interns, new grads and 0-2 year roles.

Not fresher-relevant if:
- the title has a seniority word: senior, sr, staff, principal, lead, manager, director, head, architect
  (also vp / vice president / chief), or
- the posting asks for X+ years of experience with X >= 3 (unless the title is clearly junior:
  intern, graduate, new grad, fresher, trainee, entry level, junior, apprentice).

Rows are kept in the DB with is_fresher_relevant=false; matching, evals and extraction skip them by default.

    python -m app.ingest.seniority --backfill    # set seniority + India eligibility on every stored job
"""

from __future__ import annotations

import re

SENIOR_TITLE = re.compile(
    r"\b(senior|sr|snr|staff|principal|lead|leader|manager|director|head|architect|vp|vice president|chief|"
    r"distinguished|fellow)\b\.?|\b(III|IV|V)\b|\b(SDE|SWE|MTS)[\s-]?(3|4|5)\b", re.I)
JUNIOR_TITLE = re.compile(
    r"\b(intern|internship|graduate|grad|new grad|fresher|trainee|entry[- ]level|junior|jr|apprentice|"
    r"campus|early career|university)\b", re.I)
# "5+ years", "3-5 years", "minimum 4 years", "at least 3 years" ... near "experience".
YEARS = [
    re.compile(r"\b(\d{1,2})\s*\+?\s*(?:(?:-|–|to)\s*\d{1,2}\s*)?(?:years?|yrs?)(?:'|’)?\b[^.\n]{0,60}?\bexperience", re.I),
    re.compile(r"\bexperience\b[^.\n]{0,40}?\b(\d{1,2})\s*\+?\s*(?:(?:-|–|to)\s*\d{1,2}\s*)?(?:years?|yrs?)\b", re.I),
    re.compile(r"\b(?:minimum|min\.?|at least)\s+(?:of\s+)?(\d{1,2})\s*\+?\s*(?:years?|yrs?)\b", re.I),
]
MIN_SENIOR_YEARS = 3


def min_years_required(text: str) -> int | None:
    """Smallest 'X years of experience' requirement found in the text (None if none)."""
    found = [int(m.group(1)) for rx in YEARS for m in rx.finditer(text or "") if int(m.group(1)) <= 30]
    return min(found) if found else None


# A junior title overrides these weaker words ("Product Manager Intern", "Associate Product Manager",
# "Graduate Solutions Architect"), but never senior/staff/principal/director/vp/chief.
SOFT_SENIOR = {"lead", "leader", "manager", "head", "architect"}
APM = re.compile(r"\bassociate product manager\b|\bAPM\b", re.I)


def classify(title: str, description: str) -> tuple[bool, str]:
    """(is_fresher_relevant, reason)."""
    t = title or ""
    junior = bool(JUNIOR_TITLE.search(t) or APM.search(t))
    m = SENIOR_TITLE.search(t)
    word = (m.group(0).strip(" .") if m else "").lower()
    if m and not (junior and word in SOFT_SENIOR):
        return False, f"title has '{word}'"
    if junior:
        return True, "junior title"
    yrs = min_years_required(description)
    if yrs is not None and yrs >= MIN_SENIOR_YEARS:
        return False, f"asks for {yrs}+ years of experience"
    return True, "no seniority signal" if yrs is None else f"asks for {yrs} years"


def backfill() -> dict[str, int]:
    from sqlalchemy import select

    from app.db import session_factory
    from app.ingest.store import _eligibility
    from app.models import Job

    out = {"relevant": 0, "not_relevant": 0, "india_eligible": 0, "not_india_eligible": 0}
    with session_factory()() as s:
        for job in s.execute(select(Job)).scalars():
            ok, reason = classify(job.title, job.description_text)
            job.is_fresher_relevant, job.seniority_reason = ok, reason
            out["relevant" if ok else "not_relevant"] += 1
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
