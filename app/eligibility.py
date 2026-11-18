"""Rule-based eligibility engine (spec 12). Pure Python, no LLM: Profile + JobFacts -> verdict + reasons."""

from __future__ import annotations

from collections.abc import Iterable

from app.extract.vocab import BACHELOR_LEVEL, COMPUTING_FAMILY, MASTER_LEVEL, norm_specialization
from app.schemas import EligibilityResult, JobFacts, Money, Profile, RuleResult

# Approximate conversion to INR for the pay rule only (configurable here; rough on purpose).
FX_TO_INR = {"INR": 1.0, "USD": 84.0, "EUR": 90.0, "GBP": 106.0, "SGD": 62.0, "AED": 23.0}
HOURS_PER_MONTH = 160
NCR = {"delhi", "delhi ncr", "new delhi", "gurugram", "gurgaon", "noida", "ghaziabad", "faridabad"}


def _fmt(xs: Iterable[object]) -> str:
    return ", ".join(str(x) for x in xs)


def _rr(rule: str, status: str, message: str, evidence: str | None) -> RuleResult:
    """Build a RuleResult (status validated by Pydantic)."""
    return RuleResult.model_validate({"rule": rule, "status": status, "message": message, "evidence": evidence})


def rule_degree(p: Profile, f: JobFacts) -> RuleResult:
    allowed = f.degrees_allowed.value
    ev = f.degrees_allowed.evidence
    if not allowed:
        return _rr(rule="degree", status="UNKNOWN", message="Job does not state allowed degrees", evidence=None)
    d = p.degree.upper()
    ok = (
        "ANY_GRADUATE" in allowed
        or d in allowed
        or ("BACHELORS" in allowed and d in BACHELOR_LEVEL | MASTER_LEVEL)
        or ("MASTERS" in allowed and d in MASTER_LEVEL)
    )
    if ok:
        return _rr(rule="degree", status="PASS", message=f"Job allows {_fmt(allowed)}; you have {d}", evidence=ev)
    if set(allowed) <= {"OTHER"}:
        return _rr(rule="degree", status="UNKNOWN", message="Job lists a degree outside the known codes", evidence=ev)
    return _rr(rule="degree", status="FAIL", message=f"Job allows {_fmt(allowed)}; you have {d}", evidence=ev)


def _spec_match(mine: str, listed: str) -> bool:
    a, b = norm_specialization(mine), norm_specialization(listed)
    if a == b or a in b or b in a:
        return True
    return b == "computer science" and a in COMPUTING_FAMILY


def rule_specialization(p: Profile, f: JobFacts) -> RuleResult:
    listed = f.specializations.value
    ev = f.specializations.evidence
    if listed and "all" in listed:
        return _rr(rule="specialization", status="PASS", message="Job allows all branches", evidence=ev)
    if not listed:
        return _rr(rule="specialization", status="UNKNOWN", message="Job does not state branches", evidence=None)
    if not p.specialization:
        return _rr(rule="specialization", status="UNKNOWN",
                          message=f"Job asks for {_fmt(listed)}; your specialization is not set", evidence=ev)
    if any(_spec_match(p.specialization, x) for x in listed):
        return _rr(rule="specialization", status="PASS",
                          message=f"Job allows {_fmt(listed)}; you have {p.specialization}", evidence=ev)
    return _rr(rule="specialization", status="SOFT_FAIL",
                      message=f"Job asks for {_fmt(listed)}; you have {p.specialization}", evidence=ev)


def rule_graduation_year(p: Profile, f: JobFacts) -> RuleResult:
    years = f.graduation_years.value
    ev = f.graduation_years.evidence
    if not years:
        return _rr(rule="graduation_year", status="UNKNOWN", message="Job does not state batch years", evidence=None)
    st = "PASS" if p.graduation_year in years else "FAIL"
    return _rr(rule="graduation_year", status=st,
                      message=f"Job allows batch {_fmt(list(years))}; you graduate in {p.graduation_year}", evidence=ev)


def rule_cgpa(p: Profile, f: JobFacts) -> RuleResult:
    mn = f.min_cgpa.value
    ev = f.min_cgpa.evidence
    if mn is None:
        return _rr(rule="cgpa", status="UNKNOWN", message="Job does not state a minimum CGPA", evidence=None)
    if p.cgpa is None:
        return _rr(rule="cgpa", status="UNKNOWN", message=f"Job asks for {mn:g} CGPA; your CGPA is not set",
                          evidence=ev)
    st = "PASS" if p.cgpa >= mn else "FAIL"
    return _rr(rule="cgpa", status=st, message=f"Job asks for {mn:g} CGPA; you have {p.cgpa:g}", evidence=ev)


def rule_experience(p: Profile, f: JobFacts) -> RuleResult:
    mn = f.experience_min_years.value
    ev = f.experience_min_years.evidence
    if mn is None:
        return _rr(rule="experience", status="PASS", message="Job gives no minimum experience", evidence=None)
    st = "PASS" if p.experience_years >= mn else "FAIL"
    return _rr(rule="experience", status=st,
                      message=f"Job asks for {mn:g}+ years; you have {p.experience_years:g}", evidence=ev)


def _loc_key(x: str) -> str:
    k = x.strip().lower()
    return "ncr" if k in NCR else k


def rule_location(p: Profile, f: JobFacts) -> RuleResult:
    mode, locs = f.work_mode.value, f.locations.value
    ev = f.work_mode.evidence if mode == "remote" else (f.locations.evidence or f.work_mode.evidence)
    if mode is None and not locs:
        return _rr(rule="location", status="UNKNOWN", message="Job does not state location or work mode",
                          evidence=None)
    if mode == "remote":
        if "remote" in p.work_modes:
            return _rr(rule="location", status="PASS", message="Remote job; you accept remote", evidence=ev)
    elif mode and mode not in p.work_modes:
        st = "FAIL" if p.strict_location else "SOFT_FAIL"
        return _rr(rule="location", status=st, message=f"Job is {mode}; you prefer {_fmt(list(p.work_modes))}",
                          evidence=ev)
    if locs:
        prefs = {_loc_key(x) for x in p.preferred_locations}
        if not prefs:
            return _rr(rule="location", status="PASS",
                              message=f"Job is in {_fmt(locs)}; you have no location preference", evidence=ev)
        hit = [x for x in locs if _loc_key(x) in prefs]
        if hit:
            return _rr(rule="location", status="PASS", message=f"Job is in {_fmt(hit)}, one of your locations",
                              evidence=ev)
        st = "FAIL" if p.strict_location else "SOFT_FAIL"
        return _rr(rule="location", status=st,
                          message=f"Job is in {_fmt(locs)}; you prefer {_fmt(p.preferred_locations)}", evidence=ev)
    if mode == "remote":
        st = "FAIL" if p.strict_location else "SOFT_FAIL"
        return _rr(rule="location", status=st, message="Remote job; you do not accept remote", evidence=ev)
    return _rr(rule="location", status="UNKNOWN", message=f"Job is {mode}; location not stated", evidence=ev)


def monthly_inr(m: Money, duration_months: float | None = None) -> float | None:
    """Max pay converted to INR per month, or None if currency/period unknown."""
    amount = m.max if m.max is not None else m.min
    if amount is None or not m.currency or m.currency.upper() not in FX_TO_INR or not m.period:
        return None
    inr = amount * FX_TO_INR[m.currency.upper()]
    if m.period == "month":
        return inr
    if m.period == "year":
        return inr / 12
    if m.period == "hour":
        return inr * HOURS_PER_MONTH
    if m.period == "total" and duration_months:
        return inr / duration_months
    return None


def rule_pay(p: Profile, f: JobFacts) -> RuleResult:
    m = f.pay.value
    ev = f.pay.evidence
    if p.min_monthly_pay_inr is None:
        return _rr(rule="pay", status="PASS", message="You have no minimum pay set", evidence=ev)
    if m is None:
        return _rr(rule="pay", status="UNKNOWN", message="Job does not state pay", evidence=None)
    inr = monthly_inr(m, f.duration_months.value)
    if inr is None:
        return _rr(rule="pay", status="UNKNOWN", message="Pay is stated without a clear currency or period",
                          evidence=ev)
    st = "PASS" if inr >= p.min_monthly_pay_inr else "SOFT_FAIL"
    return _rr(rule="pay", status=st,
                      message=f"Job pays about ₹{inr:,.0f}/month; your minimum is ₹{p.min_monthly_pay_inr:,}", evidence=ev)


def rule_employment_type(p: Profile, f: JobFacts) -> RuleResult:
    t = f.employment_type.value
    ev = f.employment_type.evidence
    if t is None:
        return _rr(rule="employment_type", status="UNKNOWN", message="Job does not state employment type",
                          evidence=None)
    st = "PASS" if t in p.employment_types else "SOFT_FAIL"
    return _rr(rule="employment_type", status=st,
                      message=f"Job is {t.replace('_', ' ')}; you want {_fmt([x.replace('_', ' ') for x in p.employment_types])}",
                      evidence=ev)


def check_eligibility(p: Profile, f: JobFacts) -> EligibilityResult:
    """Any FAIL -> INELIGIBLE; else any UNKNOWN or SOFT_FAIL -> CHECK; else ELIGIBLE."""
    rules = [
        rule_degree(p, f),
        rule_specialization(p, f),
        rule_graduation_year(p, f),
        rule_cgpa(p, f),
        rule_experience(p, f),
        rule_location(p, f),
        rule_pay(p, f),
        rule_employment_type(p, f),
    ]
    statuses = {r.status for r in rules}
    verdict = "INELIGIBLE" if "FAIL" in statuses else ("CHECK" if statuses & {"UNKNOWN", "SOFT_FAIL"} else "ELIGIBLE")
    return EligibilityResult(verdict=verdict, rules=rules)  # type: ignore[arg-type]
