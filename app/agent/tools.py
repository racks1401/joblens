"""Agent tools (spec 14.2). Arguments are Pydantic models (JSON Schema for the LLM), validated before running;
invalid arguments return an error message to the model instead of raising. Outputs are compact JSON built only
from stored data: unknown values stay null and eligibility verdicts are passed through unchanged.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.eligibility import check_eligibility, pay_view
from app.models import Job, JobFactsRow, TrustScore, UserMemory
from app.schemas import JobFacts, Profile

EMPLOYMENT = ("internship", "full_time", "part_time", "contract")
WORK_MODES = ("onsite", "remote", "hybrid")


class SearchFilters(BaseModel):
    # Plain strings (not enums) so a provider-side schema check accepts "full-time"; normalized below.
    employment_type: str | None = Field(default=None, description="internship | full_time | part_time | contract")
    work_mode: str | None = Field(default=None, description="onsite | remote | hybrid")
    location: str | None = None
    min_monthly_pay_inr: int | None = None
    fresher_only: bool = False
    eligible_only: bool = False

    @field_validator("employment_type", "work_mode", mode="before")
    @classmethod
    def _norm(cls, v: Any, info: Any) -> Any:
        if v in (None, ""):
            return None
        x = re.sub(r"[\s-]+", "_", str(v).strip().lower()).replace("on_site", "onsite")
        x = {"fulltime": "full_time", "intern": "internship", "parttime": "part_time"}.get(x, x)
        allowed = EMPLOYMENT if info.field_name == "employment_type" else WORK_MODES
        if x not in allowed:
            raise ValueError(f"must be one of {', '.join(allowed)}")
        return x


class SearchJobsArgs(BaseModel):
    """Search jobs for the candidate (hybrid keyword + vector search, ranked with eligibility and trust)."""

    query: str = Field(min_length=1, max_length=300)
    filters: SearchFilters = SearchFilters()
    limit: int = Field(5, ge=1, le=10)


class JobIdArgs(BaseModel):
    job_id: int


class CompareJobsArgs(BaseModel):
    job_ids: list[int] = Field(min_length=2, max_length=3)


MemoryKey = Literal["preferred_locations", "min_monthly_pay_inr", "target_roles", "avoid_companies", "notes"]


class RememberArgs(BaseModel):
    """Store a lasting user preference."""

    key: MemoryKey
    value: Any


TOOLS: dict[str, tuple[type[BaseModel], str]] = {
    "search_jobs": (SearchJobsArgs, "Search jobs for the candidate. Returns job_id, title, company, location, "
                    "final_score, eligibility verdict, trust level, url."),
    "get_job": (JobIdArgs, "Stored facts for one job (type, pay, degrees, branches, batch, CGPA, experience, skills, "
                "deadline, trust). Null means the posting does not state it."),
    "check_eligibility": (JobIdArgs, "Rule-based eligibility of the candidate for one job: ELIGIBLE / CHECK / "
                          "INELIGIBLE with one line per rule."),
    "explain_match": (JobIdArgs, "Short grounded explanation of how the job fits the candidate (matched and missing "
                      "skills, verdict)."),
    "compare_jobs": (CompareJobsArgs, "Side-by-side key facts for 2 or 3 jobs."),
    "remember": (RememberArgs, "Save a lasting preference: preferred_locations (list), min_monthly_pay_inr (int), "
                 "target_roles (list), avoid_companies (list), notes (text)."),
}


def tool_schemas() -> list[dict[str, Any]]:
    """OpenAI-style tool definitions generated from the Pydantic models."""
    out = []
    for name, (model, desc) in TOOLS.items():
        schema = model.model_json_schema()
        schema.pop("title", None)
        out.append({"type": "function", "function": {"name": name, "description": desc, "parameters": schema}})
    return out


@dataclass
class ToolContext:
    session: Session
    profile_id: uuid.UUID
    profile: Profile  # saved profile with remembered preferences applied
    memory: dict[str, Any]
    explain_llm: Any = None
    tokens: dict[str, int] = field(default_factory=lambda: {"in": 0, "out": 0})
    skipped: list[dict[str, Any]] = field(default_factory=list)  # explanations skipped this turn


@dataclass
class ToolResult:
    name: str
    args: dict[str, Any]
    output: dict[str, Any]
    ms: float
    error: str | None = None

    def content(self) -> str:
        return json.dumps(self.output, ensure_ascii=False, separators=(",", ":"), default=str)


def effective_profile(profile: Profile, memory: dict[str, Any]) -> Profile:
    """Profile with remembered preferences applied (preferred_locations, min_monthly_pay_inr, target_roles)."""
    upd: dict[str, Any] = {}
    if isinstance(memory.get("preferred_locations"), list) and memory["preferred_locations"]:
        upd["preferred_locations"] = [str(x) for x in memory["preferred_locations"]]
    if isinstance(memory.get("min_monthly_pay_inr"), int | float):
        upd["min_monthly_pay_inr"] = int(memory["min_monthly_pay_inr"])
    if isinstance(memory.get("target_roles"), list) and memory["target_roles"]:
        upd["target_roles"] = [str(x) for x in memory["target_roles"]]
    return profile.model_copy(update=upd)


def _load(s: Session, job_id: int) -> tuple[Job | None, JobFacts | None, TrustScore | None]:
    job = s.get(Job, job_id)
    if job is None:
        return None, None, None
    fr = s.get(JobFactsRow, job_id)
    return job, (JobFacts.model_validate(fr.facts) if fr else None), s.get(TrustScore, job_id)


RULE_CODES = {"money", "offplatform", "free_email", "unrealistic_pay", "missing_company"}
GHOST_CODES = {"age_60", "age_120", "repost"}


def trust_block(t: TrustScore | None, f: JobFacts | None) -> dict[str, Any]:
    """Trust level plus the scam rules and ghost-job signals that fired. Empty lists mean "checked, none fired";
    null means trust was not computed for this job."""
    flags = [r.value for r in f.red_flags] if f else []
    if t is None:
        return {"level": None, "score": None, "scam_rules_fired": None, "ghost_signals": None,
                "posting_red_flags": flags, "note": "trust not computed for this job"}
    return {"level": t.level, "score": t.score,
            "scam_rules_fired": [{"code": r["code"], "message": r["message"][:90]} for r in t.reasons
                                 if r.get("code") in RULE_CODES],
            "ghost_signals": [{"code": r["code"], "message": r["message"][:90]} for r in t.reasons
                              if r.get("code") in GHOST_CODES],
            "posting_red_flags": flags}


def elig_block(e: Any) -> dict[str, Any]:
    return {"eligibility": e.verdict,
            "needs_checking": [r.rule for r in e.rules if r.status in ("UNKNOWN", "SOFT_FAIL")],
            "failed": [r.rule for r in e.rules if r.status == "FAIL"]}


def _pay(f: JobFacts) -> dict[str, Any] | None:
    return pay_view(f)


def facts_block(f: JobFacts | None) -> dict[str, Any]:
    """Stored facts every search result carries; null means the posting (as extracted) does not state it."""
    if f is None:
        return {"pay": None, "apply_deadline": None, "work_mode": None, "employment_type": None,
                "facts": "not extracted for this job"}
    return {"pay": _pay(f), "apply_deadline": f.apply_deadline.value, "work_mode": f.work_mode.value,
            "employment_type": f.employment_type.value}


def job_card(job: Job, f: JobFacts | None, t: TrustScore | None) -> dict[str, Any]:
    f = f or JobFacts(title=job.title)
    return {
        "job_id": job.id, "title": job.title, "company": job.company or None, "url": job.url,
        "location": job.location_raw, "type": f.employment_type.value, "work_mode": f.work_mode.value,
        "locations": f.locations.value, "pay": _pay(f), "duration_months": f.duration_months.value,
        "degrees": f.degrees_allowed.value, "branches": f.specializations.value, "batch": f.graduation_years.value,
        "min_cgpa": f.min_cgpa.value, "experience_min_years": f.experience_min_years.value,
        "required_skills": (f.required_skills.value or [])[:8], "apply_deadline": f.apply_deadline.value,
        "ppo": f.ppo_or_conversion.value, "expired": job.expired, "trust": trust_block(t, f),
        "flags": [job.title.lower()] if job.title in ("Title unknown", "Needs human title") else [],
    }


NAME_STOP = {"job", "jobs", "role", "roles", "intern", "internship", "internships", "engineer", "engineering",
             "software", "developer", "for", "the", "and", "any", "with", "full", "time", "remote", "fresher",
             "freshers", "graduate", "data", "analyst", "india", "show", "find", "best", "me", "my", "a"}


def name_matches(s: Session, query: str, exclude: list[str], limit: int = 3) -> list[Job]:
    """In-scope, active, unexpired jobs whose company or title contains a distinctive query word (e.g. 'TLDP', 'Elip').

    Hybrid search ANDs keyword terms, so a short name query like "JNJ TLDP" can miss the job it names."""
    from sqlalchemy import or_

    words = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9&.+-]{2,}", query) if w.lower() not in NAME_STOP][:4]
    if not words:
        return []
    conds = [c for w in words for c in (Job.company.ilike(f"%{w}%"), Job.title.op("~*")(rf"\m{re.escape(w)}\M"))]
    q = select(Job).where(Job.is_active.is_(True), Job.in_scope.is_(True), Job.expired.is_(False), or_(*conds)).order_by(Job.id).limit(limit * 3)
    rows = _drop_excluded(list(s.execute(q).scalars().all()), exclude)
    return rows[:limit]


def _drop_excluded(jobs: list[Job], exclude: list[str]) -> list[Job]:
    ex = [e.lower() for e in exclude]
    return [j for j in jobs if not any(e in (j.company or "").lower() for e in ex)]


def search_jobs(ctx: ToolContext, a: SearchJobsArgs) -> dict[str, Any]:
    from app.search.service import Filters, search

    avoid = [str(c) for c in ctx.memory.get("avoid_companies") or []]
    flt = Filters(**a.filters.model_dump(), exclude_companies=avoid)
    res = search(ctx.session, a.query, "hybrid", limit=a.limit, filters=flt, profile=ctx.profile)
    rows = []
    for r in res["results"]:
        tb = trust_block(ctx.session.get(TrustScore, r.job_id), None)
        fr = ctx.session.get(JobFactsRow, r.job_id)
        rows.append({"job_id": r.job_id, "title": r.title, "company": r.company or None,
                     "location": r.location_raw, "final_score": r.final_score,
                     **facts_block(JobFacts.model_validate(fr.facts) if fr else None),
                     **(elig_block(r.eligibility) if r.eligibility else
                        {"eligibility": None, "needs_checking": None, "failed": None}),
                     "trust_level": tb["level"],
                     "scam_rules_fired": None if tb["scam_rules_fired"] is None else [x["code"] for x in tb["scam_rules_fired"]],
                     "ghost_signals": None if tb["ghost_signals"] is None else [x["code"] for x in tb["ghost_signals"]],
                     "url": r.url})
    seen = {r["job_id"] for r in rows}
    names = []
    for j in name_matches(ctx.session, a.query, avoid):
        if j.id in seen:
            continue
        _, f, t = _load(ctx.session, j.id)
        e = check_eligibility(ctx.profile, f or JobFacts(title=j.title))
        tb = trust_block(t, None)
        names.append({"job_id": j.id, "title": j.title, "company": j.company or None, "location": j.location_raw,
                      **facts_block(f), **elig_block(e), "trust_level": tb["level"],
                      "scam_rules_fired": None if tb["scam_rules_fired"] is None else [x["code"] for x in tb["scam_rules_fired"]],
                      "ghost_signals": None if tb["ghost_signals"] is None else [x["code"] for x in tb["ghost_signals"]],
                      "url": j.url})
    return {"jobs_returned": len(rows) + len(names), "results": rows, "name_matches": names or None,
            "excluded_companies": avoid or None}


def get_job(ctx: ToolContext, a: JobIdArgs) -> dict[str, Any]:
    job, f, t = _load(ctx.session, a.job_id)
    if job is None:
        return {"error": f"no job with id {a.job_id}"}
    return job_card(job, f, t)


def check_job_eligibility(ctx: ToolContext, a: JobIdArgs) -> dict[str, Any]:
    job, f, _ = _load(ctx.session, a.job_id)
    if job is None:
        return {"error": f"no job with id {a.job_id}"}
    e = check_eligibility(ctx.profile, f or JobFacts(title=job.title))
    b = elig_block(e)
    return {"job_id": job.id, "title": job.title, "company": job.company or None, "verdict": e.verdict,
            "needs_checking": b["needs_checking"], "failed": b["failed"],
            "rules": [{"rule": r.rule, "status": r.status, "message": r.message} for r in e.rules]}


def explain(ctx: ToolContext, a: JobIdArgs) -> dict[str, Any]:
    from app.explain import explain_match

    job, f, t = _load(ctx.session, a.job_id)
    if job is None:
        return {"error": f"no job with id {a.job_id}"}
    out = explain_match(ctx.session, ctx.profile_id, ctx.profile, job, f or JobFacts(title=job.title), t,
                        ctx.explain_llm, ctx.tokens)
    if "error" in out:
        ctx.skipped.append({"job_id": job.id, "reason": out["error"]})
        return {"job_id": job.id, "title": job.title, "explanation_skipped": True, "reason": out.pop("error")}
    return {"job_id": job.id, "title": job.title, **out}


def compare_jobs(ctx: ToolContext, a: CompareJobsArgs) -> dict[str, Any]:
    rows = []
    for jid in a.job_ids:
        job, f, t = _load(ctx.session, jid)
        if job is None:
            rows.append({"job_id": jid, "error": "not found"})
            continue
        c = job_card(job, f, t)
        e = check_eligibility(ctx.profile, f or JobFacts(title=job.title))
        rows.append({k: c[k] for k in ("job_id", "title", "company", "type", "work_mode", "locations", "pay",
                                       "duration_months", "min_cgpa", "experience_min_years", "required_skills",
                                       "trust")} | elig_block(e))
    return {"jobs_compared": len(rows), "jobs": rows}


def remember(ctx: ToolContext, a: RememberArgs) -> dict[str, Any]:
    from app.demo import DEMO_PROFILE_ID

    if ctx.profile_id == DEMO_PROFILE_ID:  # shared by every visitor of the public demo
        return {"error": "The shared demo profile cannot store preferences. Save your own profile on the Profile "
                         "page, then ask again."}
    v = a.value
    if a.key in ("preferred_locations", "target_roles", "avoid_companies") and isinstance(v, str):
        v = [x.strip() for x in v.split(",") if x.strip()]
    if a.key == "min_monthly_pay_inr":
        try:
            v = int(float(str(v).replace(",", "")))
        except ValueError:
            return {"error": "min_monthly_pay_inr must be a number"}
    stmt = insert(UserMemory).values(profile_id=ctx.profile_id, key=a.key, value=v)
    ctx.session.execute(stmt.on_conflict_do_update(index_elements=["profile_id", "key"], set_={"value": v}))
    ctx.session.commit()
    ctx.memory[a.key] = v
    ctx.profile = effective_profile(ctx.profile, ctx.memory)
    return {"saved": {a.key: v}}


IMPL = {"search_jobs": search_jobs, "get_job": get_job, "check_eligibility": check_job_eligibility,
        "explain_match": explain, "compare_jobs": compare_jobs, "remember": remember}


def execute_tool(name: str, raw_args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Validate arguments and run a tool. Never raises: errors come back as {"error": ...} for the model."""
    t0 = time.perf_counter()
    if name not in TOOLS:
        return ToolResult(name, raw_args, {"error": f"unknown tool {name}"}, 0.0, "unknown tool")
    model, _ = TOOLS[name]
    try:
        args = model.model_validate(raw_args)
    except ValidationError as e:
        msg = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())[:300]
        return ToolResult(name, raw_args, {"error": f"invalid arguments: {msg}"}, (time.perf_counter() - t0) * 1000, msg)
    try:
        out = IMPL[name](ctx, args)  # type: ignore[operator]
    except Exception as e:  # noqa: BLE001 - reported to the model, logged in the trace
        out = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
    out = json.loads(json.dumps(out, default=str))  # JSON-safe (dates -> ISO strings) for the model and the trace
    return ToolResult(name, args.model_dump(), out, (time.perf_counter() - t0) * 1000, out.get("error"))


def load_memory(s: Session, profile_id: uuid.UUID) -> dict[str, Any]:
    return {m.key: m.value for m in s.execute(select(UserMemory).where(UserMemory.profile_id == profile_id)).scalars()}
