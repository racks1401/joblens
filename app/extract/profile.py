"""Resume PDF -> text (pypdf) -> ProfileDraft (LLM, prompt profile_v1). The user reviews before saving."""

from __future__ import annotations

import io
import json
import logging
from functools import lru_cache
from typing import Any

from pydantic import ValidationError
from pypdf import PdfReader

from app.extract.prompting import build_messages
from app.extract.vocab import norm_degree, norm_location, norm_skill
from app.llm.client import LLMClient, LLMJSONError
from app.logs import log_event
from app.schemas import Profile, ProfileDraft, SkillItem

log = logging.getLogger("joblens.profile")
MIN_RESUME_CHARS = 200


class ResumeTextTooShort(ValueError):
    """Extracted text under 200 chars: probably a scanned PDF."""


def pdf_to_text(data: bytes) -> str:
    """Extract text from all pages."""
    reader = PdfReader(io.BytesIO(data))
    return "\n".join((p.extract_text() or "") for p in reader.pages).strip()


@lru_cache
def profile_schema_json() -> str:
    return json.dumps(ProfileDraft.model_json_schema(), separators=(",", ":"))


def _norm_skills(items: list[SkillItem]) -> list[SkillItem]:
    seen: dict[str, SkillItem] = {}
    for it in items:
        n = norm_skill(it.name)
        if n and n not in seen:
            seen[n] = SkillItem(name=n, level=it.level)
    return list(seen.values())


def normalize_draft(d: ProfileDraft) -> ProfileDraft:
    d.skills = _norm_skills(d.skills)
    d.degree = norm_degree(d.degree) if d.degree else None
    d.preferred_locations = list(dict.fromkeys(norm_location(x) for x in d.preferred_locations if x))
    return d


def normalize_profile(p: Profile) -> Profile:
    """Normalize a confirmed profile the same way as job facts."""
    p.skills = _norm_skills(p.skills)
    p.degree = norm_degree(p.degree)
    p.preferred_locations = list(dict.fromkeys(norm_location(x) for x in p.preferred_locations if x))
    p.target_roles = [r.strip().lower() for r in p.target_roles if r.strip()]
    return p


def _coerce(raw: dict[str, Any]) -> dict[str, Any]:
    out = dict(raw)
    sk = out.get("skills") or []
    out["skills"] = [{"name": s} if isinstance(s, str) else s for s in sk]
    for f in ("target_roles", "preferred_locations", "work_modes", "employment_types"):
        if isinstance(out.get(f), str):
            out[f] = [out[f]]
        if out.get(f) is None:
            out[f] = []
    if out.get("strict_location") is None:
        out["strict_location"] = False
    return out


def extract_profile(resume_text: str, llm: LLMClient, prompt_version: str = "profile_v1") -> ProfileDraft:
    """LLM fills ProfileDraft; fields not in the resume stay null. Retries twice on invalid output."""
    if len(resume_text) < MIN_RESUME_CHARS:
        raise ResumeTextTooShort("Could not read enough text from this PDF; it may be a scanned image.")
    messages: list[dict[str, Any]] = build_messages(prompt_version, json_schema=profile_schema_json(),
                                                    resume_text=resume_text[:20000])
    for attempt in range(3):
        try:
            raw, _ = llm.chat_json(messages, ProfileDraft, temperature=0.0, prompt_version=prompt_version)
            return normalize_draft(ProfileDraft.model_validate(_coerce(raw)))
        except (LLMJSONError, ValidationError) as e:
            log_event(log, "profile_invalid", level=logging.WARNING, attempt=attempt + 1, error=str(e)[:300])
            messages = messages + [{"role": "user", "content": f"Invalid output: {str(e)[:1000]}. Return corrected JSON only."}]
    return ProfileDraft()
