from typing import Any

import pytest

from app.eligibility import check_eligibility, monthly_inr
from app.schemas import Evidenced, JobFacts, Money, Profile, SkillItem


def prof(**kw: Any) -> Profile:
    base: dict[str, Any] = dict(degree="MCA", specialization="AI & IoT", graduation_year=2027, cgpa=8.0,
                                skills=[SkillItem(name="python")], target_roles=["swe intern"],
                                preferred_locations=["Bengaluru"], work_modes=["onsite", "remote", "hybrid"],
                                min_monthly_pay_inr=20000, employment_types=["internship", "full_time"])
    base.update(kw)
    return Profile(**base)


def facts(**kw: Any) -> JobFacts:
    return JobFacts(title="T", **{k: Evidenced(value=v, evidence=f"ev-{k}") for k, v in kw.items()})


def rule(res: Any, name: str) -> Any:
    return next(r for r in res.rules if r.rule == name)


FULL = dict(degrees_allowed=["BTECH", "MCA"], specializations=["all"], graduation_years=[2026, 2027], min_cgpa=7.0,
            experience_min_years=0.0, work_mode="onsite", locations=["Bengaluru"],
            pay=Money(min=25000, max=25000, currency="INR", period="month"), employment_type="internship")


def test_all_pass_is_eligible_with_evidence() -> None:
    r = check_eligibility(prof(), facts(**FULL))
    assert r.verdict == "ELIGIBLE" and all(x.status == "PASS" for x in r.rules)
    assert rule(r, "degree").message == "Job allows BTECH, MCA; you have MCA"
    assert rule(r, "degree").evidence == "ev-degrees_allowed"


@pytest.mark.parametrize("override,name,status,verdict", [
    ({"degrees_allowed": ["BTECH"]}, "degree", "FAIL", "INELIGIBLE"),
    ({"degrees_allowed": ["ANY_GRADUATE"]}, "degree", "PASS", "ELIGIBLE"),
    ({"degrees_allowed": ["BACHELORS"]}, "degree", "PASS", "ELIGIBLE"),  # a master's holder has a bachelor's
    ({"degrees_allowed": ["MASTERS"]}, "degree", "PASS", "ELIGIBLE"),
    ({"degrees_allowed": None}, "degree", "UNKNOWN", "CHECK"),
    ({"specializations": ["Mechanical"]}, "specialization", "SOFT_FAIL", "CHECK"),
    ({"specializations": ["Computer Science"]}, "specialization", "PASS", "ELIGIBLE"),  # AI counts as computing
    ({"specializations": None}, "specialization", "UNKNOWN", "CHECK"),
    ({"graduation_years": [2025]}, "graduation_year", "FAIL", "INELIGIBLE"),
    ({"graduation_years": None}, "graduation_year", "UNKNOWN", "CHECK"),
    ({"min_cgpa": 8.5}, "cgpa", "FAIL", "INELIGIBLE"),
    ({"min_cgpa": None}, "cgpa", "UNKNOWN", "CHECK"),
    ({"experience_min_years": 2.0}, "experience", "FAIL", "INELIGIBLE"),
    ({"experience_min_years": None}, "experience", "PASS", "ELIGIBLE"),
    ({"locations": ["Chennai"]}, "location", "SOFT_FAIL", "CHECK"),
    ({"work_mode": "remote", "locations": None}, "location", "PASS", "ELIGIBLE"),
    ({"work_mode": None, "locations": None}, "location", "UNKNOWN", "CHECK"),
    ({"pay": Money(min=10000, max=15000, currency="INR", period="month")}, "pay", "SOFT_FAIL", "CHECK"),
    ({"pay": Money(min=300000, max=360000, currency="INR", period="year")}, "pay", "PASS", "ELIGIBLE"),
    ({"pay": Money(min=15000, currency=None, period="month")}, "pay", "UNKNOWN", "CHECK"),
    ({"pay": None}, "pay", "UNKNOWN", "CHECK"),
    ({"employment_type": "contract"}, "employment_type", "SOFT_FAIL", "CHECK"),
    ({"employment_type": None}, "employment_type", "UNKNOWN", "CHECK"),
])
def test_each_rule(override: dict[str, Any], name: str, status: str, verdict: str) -> None:
    r = check_eligibility(prof(), facts(**{**FULL, **override}))
    assert rule(r, name).status == status
    assert r.verdict == verdict


def test_profile_side_missing_and_strict_location() -> None:
    r = check_eligibility(prof(cgpa=None, specialization=None), facts(**{**FULL, "specializations": ["Computer Science"]}))
    assert rule(r, "cgpa").status == "UNKNOWN" and "not set" in rule(r, "cgpa").message
    assert rule(r, "specialization").status == "UNKNOWN"
    r2 = check_eligibility(prof(strict_location=True), facts(**{**FULL, "locations": ["Chennai"]}))
    assert rule(r2, "location").status == "FAIL" and r2.verdict == "INELIGIBLE"
    r3 = check_eligibility(prof(work_modes=["onsite"]), facts(**{**FULL, "work_mode": "remote", "locations": None}))
    assert rule(r3, "location").status == "SOFT_FAIL"
    r4 = check_eligibility(prof(preferred_locations=["Delhi NCR"]), facts(**{**FULL, "locations": ["Gurugram"]}))
    assert rule(r4, "location").status == "PASS"  # NCR group
    r5 = check_eligibility(prof(min_monthly_pay_inr=None), facts(**{**FULL, "pay": None}))
    assert rule(r5, "pay").status == "PASS"


def test_monthly_inr_conversions() -> None:
    assert monthly_inr(Money(max=1200000, currency="INR", period="year")) == 100000
    assert monthly_inr(Money(min=10, currency="USD", period="hour")) == 10 * 84 * 160
    assert monthly_inr(Money(max=60000, currency="INR", period="total"), 6) == 10000
    assert monthly_inr(Money(max=60000, currency="INR", period="total")) is None
    assert monthly_inr(Money(max=1, currency="XYZ", period="month")) is None
