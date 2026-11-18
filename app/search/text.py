"""Search texts (spec 11.1) and profile query text (spec 11.2)."""

from __future__ import annotations

from typing import Any

from app.schemas import JobFacts, Profile

VARIANTS = ("raw", "structured")


def raw_text(title: str, description: str) -> str:
    """raw: title + newline + first 2,000 chars of the description."""
    return f"{title}\n{description[:2000]}"


def structured_text(title: str, company: str | None, description: str, facts: JobFacts | None) -> str:
    """structured: built from JobFacts; unknown parts are omitted."""
    lines = [f"Title: {title}"]
    if company:
        lines.append(f"Company: {company}")
    if facts:
        parts = []
        if facts.employment_type.value:
            parts.append(f"Type: {facts.employment_type.value.replace('_', ' ')}")
        if facts.work_mode.value:
            parts.append(f"Mode: {facts.work_mode.value}")
        if facts.locations.value:
            parts.append(f"Locations: {', '.join(facts.locations.value)}")
        if parts:
            lines.append(" | ".join(parts))
        if facts.required_skills.value:
            lines.append(f"Required skills: {', '.join(facts.required_skills.value)}")
        if facts.nice_to_have_skills.value:
            lines.append(f"Nice to have: {', '.join(facts.nice_to_have_skills.value)}")
        elig = []
        if facts.degrees_allowed.value:
            elig.append("degrees " + ", ".join(facts.degrees_allowed.value))
        if facts.graduation_years.value:
            elig.append("batch " + ", ".join(str(y) for y in facts.graduation_years.value))
        if facts.experience_min_years.value is not None:
            elig.append(f"{facts.experience_min_years.value:g}+ years experience")
        if facts.fresher_friendly.value:
            elig.append("fresher friendly")
        if elig:
            lines.append("Eligibility: " + "; ".join(elig))
    lines.append(f"Summary: {description[:400]}")
    return "\n".join(lines)


def build_job_search_text(title: str, company: str | None, description: str, facts: JobFacts | None,
                          variant: str) -> str:
    """Text for one embedding variant."""
    if variant == "raw":
        return raw_text(title, description)
    return structured_text(title, company, description, facts)


def _join_or(items: list[str], word: str = "or") -> str:
    return f" {word} ".join(items)


def build_profile_query_text(p: Profile) -> str:
    """Profile query text in the spec 11.2 layout."""
    lines = []
    if p.target_roles:
        lines.append("Target roles: " + ", ".join(p.target_roles))
    if p.skills:
        lines.append("Skills: " + ", ".join(s.name for s in p.skills))
    deg = p.degree + (f" ({p.specialization})" if p.specialization else "")
    lines.append(f"Degree: {deg}, graduating {p.graduation_year}")
    prefs = [_join_or([t.replace("_", " ") for t in p.employment_types]), _join_or(list(p.work_modes))]
    if p.preferred_locations:
        prefs.append(", ".join(p.preferred_locations))
    lines.append("Preferences: " + "; ".join(x for x in prefs if x))
    return "\n".join(lines)


def profile_keyword_query(p: Profile) -> str:
    """Target roles plus the top 10 skills joined with OR (websearch_to_tsquery syntax)."""
    terms = [r for r in p.target_roles if r] + [s.name for s in p.skills[:10]]
    return " OR ".join(terms)


def facts_from_row(data: dict[str, Any] | None) -> JobFacts | None:
    return JobFacts.model_validate(data) if data else None
