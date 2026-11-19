"""Grounded match explanation (spec 14.1). LLM (explain_v1) writes it; code keeps only matched/missing skills that
exist in the inputs and forces the stated eligibility verdict and trust level to the computed ones. Cached in the
explanations table by (profile updated_at, job content_hash, prompt_version).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.eligibility import check_eligibility
from app.extract.prompting import build_messages
from app.llm.client import BudgetExceeded, LLMJSONError, LLMResult
from app.models import ExplanationRow, Job, ProfileRow, TrustScore
from app.schemas import JobFacts, MatchExplanation, Profile


def _inputs(profile: Profile, facts: JobFacts, elig: Any, trust: TrustScore | None) -> dict[str, str]:
    p = {"degree": profile.degree, "specialization": profile.specialization, "graduation_year": profile.graduation_year,
         "cgpa": profile.cgpa, "experience_years": profile.experience_years, "skills": [s.name for s in profile.skills]}
    f = {"title": facts.title, "required_skills": facts.required_skills.value, "nice_to_have_skills": facts.nice_to_have_skills.value,
         "experience_min_years": facts.experience_min_years.value, "employment_type": facts.employment_type.value}
    t = {"level": trust.level, "score": trust.score} if trust else {"level": "not computed"}
    return {"profile_json": json.dumps(p), "facts_json": json.dumps(f),
            "eligibility_json": json.dumps({"verdict": elig.verdict, "rules": [r.message for r in elig.rules]}),
            "trust_json": json.dumps(t)}


def ground(expl: MatchExplanation, profile: Profile, facts: JobFacts) -> MatchExplanation:
    ps = {s.name.lower() for s in profile.skills}
    req = {s.lower() for s in facts.required_skills.value or []}
    nice = {s.lower() for s in facts.nice_to_have_skills.value or []}
    expl.matched_skills = [s for s in expl.matched_skills if s.lower() in ps and s.lower() in req | nice]
    expl.missing_skills = [s for s in expl.missing_skills if s.lower() in req and s.lower() not in ps]
    return expl


def explain_match(s: Session, profile_id: uuid.UUID, profile: Profile, job: Job, facts: JobFacts,
                  trust: TrustScore | None, llm: Any, tokens: dict[str, int] | None = None) -> dict[str, Any]:
    from app.config import get_settings
    from app.llm.client import get_llm

    pv = get_settings().cfg.prompts.explain
    prow = s.get(ProfileRow, profile_id)
    key = {"profile_updated_at": str(prow.updated_at) if prow else None, "content_hash": job.content_hash, "prompt": pv}
    cached = s.get(ExplanationRow, {"profile_id": profile_id, "job_id": job.id})
    if cached and cached.data.get("_key") == key:
        return {k: v for k, v in cached.data.items() if k != "_key"} | {"cached": True}
    elig = check_eligibility(profile, facts)
    llm = llm or get_llm("explain")
    try:
        raw, res = llm.chat_json(build_messages(pv, **_inputs(profile, facts, elig, trust)), MatchExplanation,
                                 temperature=0.0, prompt_version=pv, essential=False)
        if tokens is not None:
            tokens["in"] += res.tokens_in
            tokens["out"] += res.tokens_out
        expl = ground(MatchExplanation.model_validate(raw), profile, facts)
    except BudgetExceeded as e:
        return {"error": str(e)}
    except (LLMJSONError, ValidationError) as e:
        return {"error": f"explanation unavailable ({type(e).__name__})"}
    data = expl.model_dump() | {"eligibility": elig.verdict, "trust_level": trust.level if trust else None}
    if prow is not None:
        stmt = insert(ExplanationRow).values(profile_id=profile_id, job_id=job.id, data=data | {"_key": key},
                                             model=llm.model, prompt_version=pv)
        s.execute(stmt.on_conflict_do_update(index_elements=["profile_id", "job_id"],
                                             set_={"data": data | {"_key": key}, "model": llm.model}))
        s.commit()
    return data


def fake_explain_handler(messages: list[dict[str, Any]], tools: Any) -> LLMResult:
    """Fake-LLM explanation: deterministic from the JSON inputs in the prompt."""
    text = str(messages[-1].get("content") or "")

    def grab(label: str) -> dict[str, Any]:
        line = next((ln for ln in text.splitlines() if ln.startswith(label)), "")
        try:
            return json.loads(line[len(label):].strip() or "{}")
        except json.JSONDecodeError:
            return {}

    p, f, e = grab("Profile:"), grab("Job facts:"), grab("Eligibility:")
    ps = {x.lower() for x in p.get("skills") or []}
    req = [x for x in f.get("required_skills") or []]
    matched = [x for x in req if x.lower() in ps]
    missing = [x for x in req if x.lower() not in ps]
    verdict = "strong" if req and len(matched) >= len(missing) else ("possible" if matched else "weak")
    return LLMResult(content=json.dumps({"matched_skills": matched, "missing_skills": missing, "verdict": verdict,
                                         "summary": f"Eligibility {e.get('verdict')}. Matches {len(matched)} of {len(req)} required skills."}))
