"""Pydantic models shared by ingestion, extraction, matching and the API."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")

Source = Literal["greenhouse", "lever", "ashby", "paste"]


class NormalizedJob(BaseModel):
    """A job posting mapped from any source (spec 7.2)."""

    source: Source
    source_job_id: str
    company: str
    title: str
    location_raw: str | None = None
    url: str | None = None
    description_text: str
    posted_at: datetime | None = None
    fetched_at: datetime


class CompanyCfg(BaseModel):
    """One entry of config/companies.yaml."""

    name: str
    source: Literal["greenhouse", "lever", "ashby"]
    token: str
    country_hint: str | None = None


# ---------- JobFacts (spec 9.1) ----------


class Evidenced(BaseModel, Generic[T]):
    """A value plus a verbatim evidence quote from the posting."""

    value: T | None = None
    evidence: str | None = None


class Money(BaseModel):
    min: float | None = None
    max: float | None = None
    currency: str | None = None
    period: Literal["month", "year", "hour", "total"] | None = None


EmploymentType = Literal["internship", "full_time", "part_time", "contract"]
WorkMode = Literal["onsite", "remote", "hybrid"]


class JobFacts(BaseModel):
    """Structured facts extracted from a posting; every non-null field carries evidence."""

    title: str
    company: str | None = None
    employment_type: Evidenced[EmploymentType] = Evidenced()
    work_mode: Evidenced[WorkMode] = Evidenced()
    locations: Evidenced[list[str]] = Evidenced()
    duration_months: Evidenced[float] = Evidenced()
    pay: Evidenced[Money] = Evidenced()
    degrees_allowed: Evidenced[list[str]] = Evidenced()
    specializations: Evidenced[list[str]] = Evidenced()
    graduation_years: Evidenced[list[int]] = Evidenced()
    min_cgpa: Evidenced[float] = Evidenced()
    experience_min_years: Evidenced[float] = Evidenced()
    fresher_friendly: Evidenced[bool] = Evidenced()
    required_skills: Evidenced[list[str]] = Evidenced()
    nice_to_have_skills: Evidenced[list[str]] = Evidenced()
    apply_deadline: Evidenced[date] = Evidenced()
    ppo_or_conversion: Evidenced[bool] = Evidenced()
    red_flags: list[Evidenced[str]] = []


EVIDENCED_FIELDS = [
    "employment_type", "work_mode", "locations", "duration_months", "pay", "degrees_allowed",
    "specializations", "graduation_years", "min_cgpa", "experience_min_years", "fresher_friendly",
    "required_skills", "nice_to_have_skills", "apply_deadline", "ppo_or_conversion",
]


# ---------- Profile (spec 10) ----------


class SkillItem(BaseModel):
    name: str
    level: Literal["beginner", "intermediate", "advanced"] | None = None


class Profile(BaseModel):
    """User-confirmed profile used for matching."""

    name: str | None = None
    degree: str
    specialization: str | None = None
    graduation_year: int
    cgpa: float | None = Field(default=None, ge=0, le=10)
    experience_years: float = 0
    skills: list[SkillItem]
    target_roles: list[str]
    preferred_locations: list[str] = []
    work_modes: list[WorkMode] = ["onsite", "remote", "hybrid"]
    min_monthly_pay_inr: int | None = None
    employment_types: list[str] = ["internship", "full_time"]
    strict_location: bool = False


class ProfileDraft(BaseModel):
    """Profile as extracted from a resume: every field optional, for the user to review."""

    name: str | None = None
    degree: str | None = None
    specialization: str | None = None
    graduation_year: int | None = None
    cgpa: float | None = None
    experience_years: float | None = None
    skills: list[SkillItem] = []
    target_roles: list[str] = []
    preferred_locations: list[str] = []
    work_modes: list[WorkMode] = []
    min_monthly_pay_inr: int | None = None
    employment_types: list[str] = []
    strict_location: bool = False


# ---------- Eligibility (spec 12) ----------

RuleStatus = Literal["PASS", "FAIL", "SOFT_FAIL", "UNKNOWN"]
Verdict = Literal["ELIGIBLE", "CHECK", "INELIGIBLE"]


class RuleResult(BaseModel):
    rule: str
    status: RuleStatus
    message: str
    evidence: str | None = None


class EligibilityResult(BaseModel):
    verdict: Verdict
    rules: list[RuleResult]
    # False when the job has no usable extracted requirements (no facts row yet, or extraction failed). Every rule is
    # then UNKNOWN and the verdict is CHECK: missing facts are never read as "the posting states nothing".
    facts_extracted: bool = True


# ---------- Trust (spec 13) ----------


class TrustReason(BaseModel):
    code: str
    message: str
    delta: int


class TrustResult(BaseModel):
    score: int
    level: Literal["ok", "caution", "risky"]
    reasons: list[TrustReason]
    scam_prob: float | None = None


# ---------- Explanation (spec 14.1) ----------


class MatchExplanation(BaseModel):
    matched_skills: list[str]
    missing_skills: list[str]
    summary: str
    verdict: Literal["strong", "possible", "weak"]


# ---------- API payloads ----------


class PasteJobIn(BaseModel):
    text: str = Field(min_length=20, max_length=50000)
    url: str | None = None
    company: str | None = None


class JobOut(BaseModel):
    id: int
    source: str
    company: str
    title: str
    location_raw: str | None
    url: str | None
    posted_at: datetime | None
    first_seen_at: datetime
    last_seen_at: datetime
    is_active: bool
    repost_count: int
    description_text: str | None = None
    facts: dict[str, Any] | None = None
    facts_valid: bool | None = None
    trust: TrustResult | None = None
    flags: list[str] = []  # e.g. "title unknown", "needs human title" (pasted jobs)
    related_job_ids: list[int] = []  # other roles split from the same pasted post
    expired: bool = False  # apply deadline has passed (kept, hidden from default search)
    eligibility: EligibilityResult | None = None  # only when GET /jobs/{id}?profile_id= is given
