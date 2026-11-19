"""Agent tools (spec 14.2). Arguments are Pydantic models (JSON Schema for the LLM), validated before running;
invalid arguments return an error message to the model instead of raising. Outputs are compact JSON built only
from stored data: unknown values stay null and eligibility verdicts are passed through unchanged.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.eligibility import check_eligibility, monthly_inr
from app.models import Job, JobFactsRow, TrustScore, UserMemory
from app.schemas import JobFacts, Profile


class SearchFilters(BaseModel):
    employment_type: Literal["internship", "full_time", "part_time", "contract"] | None = None
    work_mode: Literal["onsite", "remote", "hybrid"] | None = None
    location: str | None = None
    min_monthly_pay_inr: int | None = None
    fresher_only: bool = False
    eligible_only: bool = False


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


def _trust(t: TrustScore | None) -> dict[str, Any] | None:
    if t is None:
        return None
    return {"score": t.score, "level": t.level,
            "reasons": [r["message"][:90] for r in t.reasons if r.get("code") != "classifier"]}


def _pay(f: JobFacts) -> dict[str, Any] | None:
    m = f.pay.value
    if m is None:
        return None
    inr = monthly_inr(m, f.duration_months.value)
    return {"min": m.min, "max": m.max, "currency": m.currency, "period": m.period,
            "approx_inr_per_month": round(inr) if inr is not None else None}


def job_card(job: Job, f: JobFacts | None, t: TrustScore | None) -> dict[str, Any]:
    f = f or JobFacts(title=job.title)
    return {
        "job_id": job.id, "title": job.title, "company": job.company or None, "url": job.url,
        "location": job.location_raw, "type": f.employment_type.value, "work_mode": f.work_mode.value,
        "locations": f.locations.value, "pay": _pay(f), "duration_months": f.duration_months.value,
        "degrees": f.degrees_allowed.value, "branches": f.specializations.value, "batch": f.graduation_years.value,
        "min_cgpa": f.min_cgpa.value, "experience_min_years": f.experience_min_years.value,
        "required_skills": (f.required_skills.value or [])[:8], "apply_deadline": f.apply_deadline.value,
        "ppo": f.ppo_or_conversion.value, "red_flags": [r.value for r in f.red_flags], "trust": _trust(t),
        "flags": [job.title.lower()] if job.title in ("Title unknown", "Needs human title") else [],
    }


def search_jobs(ctx: ToolContext, a: SearchJobsArgs) -> dict[str, Any]:
    from app.search.service import Filters, search

    avoid = [str(c) for c in ctx.memory.get("avoid_companies") or []]
    flt = Filters(**a.filters.model_dump(), exclude_companies=avoid)
    res = search(ctx.session, a.query, "hybrid", limit=a.limit, filters=flt, profile=ctx.profile)
    return {"results": [{"job_id": r.job_id, "title": r.title, "company": r.company or None,
                         "location": r.location_raw, "final_score": r.final_score,
                         "eligibility": r.eligibility.verdict if r.eligibility else None,
                         "trust_level": r.trust.level if r.trust else None, "url": r.url}
                        for r in res["results"]],
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
    return {"job_id": job.id, "title": job.title, "company": job.company or None, "verdict": e.verdict,
            "rules": [{"rule": r.rule, "status": r.status, "message": r.message} for r in e.rules]}


def explain(ctx: ToolContext, a: JobIdArgs) -> dict[str, Any]:
    from app.explain import explain_match

    job, f, t = _load(ctx.session, a.job_id)
    if job is None:
        return {"error": f"no job with id {a.job_id}"}
    out = explain_match(ctx.session, ctx.profile_id, ctx.profile, job, f or JobFacts(title=job.title), t,
                        ctx.explain_llm, ctx.tokens)
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
                                       "duration_months", "min_cgpa", "experience_min_years", "required_skills")}
                    | {"eligibility": e.verdict, "trust_level": (c["trust"] or {}).get("level")})
    return {"jobs": rows}


def remember(ctx: ToolContext, a: RememberArgs) -> dict[str, Any]:
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
    return ToolResult(name, args.model_dump(), out, (time.perf_counter() - t0) * 1000, out.get("error"))


def load_memory(s: Session, profile_id: uuid.UUID) -> dict[str, Any]:
    return {m.key: m.value for m in s.execute(select(UserMemory).where(UserMemory.profile_id == profile_id)).scalars()}
