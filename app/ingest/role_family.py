"""Rule-based role family (no LLM): engineering | data_ml | product | design | other.

Title keywords decide; for generic titles ("Intern", "Associate", "Graduate Trainee") the extracted
required skills break the tie. Customer-facing "engineer" titles (sales/solutions/support/field engineer)
count as other. Matching, evals and drafting use only TECH_FAMILIES by default (via jobs.in_scope).

    python -m app.ingest.role_family --backfill
"""

from __future__ import annotations

import re
from collections.abc import Iterable

TECH_FAMILIES = ("engineering", "data_ml", "product")

_R = lambda p: re.compile(p, re.I)  # noqa: E731

CUSTOMER_FACING = _R(r"\b(sales|solutions?|pre-?sales|customer|support|field|implementation|technical account|"
                     r"account|partner|success|onboarding|deployment strategist|value)\b.*\b(engineer|consultant|"
                     r"architect|specialist|manager|executive)\b|\bsales engineer\b|\bsupport engineer\b|"
                     r"\bsolutions? (engineer|consultant)\b|\bcustomer engineer\b")
DESIGN = _R(r"\b(designer|ux|ui/ux|ui designer|user experience|user research(er)?|product design|visual design|"
            r"interaction design|brand design)\b")
PRODUCT = _R(r"\b(product manager|product management|product owner|associate product manager|apm|"
             r"product analyst|technical program manager|tpm|product operations|product intern)\b")
DATA_ML = _R(r"\b(data scien\w*|data analy\w*|data engineer\w*|analytics engineer|machine learning|ml|mlops|"
             r"ai|artificial intelligence|deep learning|nlp|computer vision|research (scientist|engineer|intern)|"
             r"applied scientist|quant(itative)? (researcher|analyst|developer)|business intelligence|bi analyst|"
             r"llm|genai|generative)\b")
ENGINEERING = _R(r"\b(engineer\w*|developer|programmer|sde|swe|software|devops|sre|site reliability|backend|"
                 r"back-end|frontend|front-end|full[- ]?stack|mobile|android|ios|qa|sdet|test automation|"
                 r"security|infrastructure|platform|cloud|firmware|embedded|kernel|systems|mts|"
                 r"member of technical staff|technical staff|forward deployed|hardware|silicon|vlsi|network)\b")
NON_TECH = _R(r"\b(marketing|sales|recruit\w*|talent|people|hr|human resources|finance|financial|accounting|legal|"
              r"account|customer|copy\w*|content|communications|operations|business development|partnership\w*|"
              r"community|support|growth|brand|events?|office|admin\w*|procurement|payroll|tax|audit)\b")
GENERIC = _R(r"\b(intern|internship|graduate|trainee|associate|apprentice|fellow|new grad|campus|early career)\b")

PROGRAMMING = {"python", "java", "javascript", "typescript", "go", "rust", "c++", "c#", "kotlin", "swift", "scala",
               "react", "node.js", "django", "flask", "fastapi", "spring boot", "android", "ios", "kubernetes",
               "docker", "aws", "linux", "rest apis", "microservices", "data structures", "algorithms"}
DATA_SKILLS = {"machine learning", "deep learning", "pytorch", "tensorflow", "scikit-learn", "pandas", "numpy",
               "statistics", "llm", "natural language processing", "computer vision", "spark", "airflow", "etl",
               "data analysis", "power bi", "tableau", "sql"}


def classify_role(title: str, required_skills: Iterable[str] | None = None) -> tuple[str, str]:
    """(family, reason)."""
    t = title or ""
    if CUSTOMER_FACING.search(t):
        return "other", "customer-facing title"
    for fam, rx in (("design", DESIGN), ("product", PRODUCT), ("data_ml", DATA_ML), ("engineering", ENGINEERING)):
        m = rx.search(t)
        if m:
            return fam, f"title has '{m.group(0).lower()}'"
    if GENERIC.search(t) and not NON_TECH.search(t):
        sk = {s.lower() for s in (required_skills or [])}
        if len(sk & DATA_SKILLS - {"sql"}) >= 2:
            return "data_ml", "generic title; data/ML skills required"
        if len(sk & PROGRAMMING) >= 2:
            return "engineering", "generic title; programming skills required"
    return "other", "no tech title keyword"


def backfill() -> dict[str, int]:
    from sqlalchemy import select

    from app.db import session_factory
    from app.models import Job, JobFactsRow

    out: dict[str, int] = {}
    with session_factory()() as s:
        for job, facts in s.execute(select(Job, JobFactsRow.facts).outerjoin(JobFactsRow, JobFactsRow.job_id == Job.id)):
            skills = ((facts or {}).get("required_skills") or {}).get("value") or []
            job.role_family, job.role_reason = classify_role(job.title, skills)
            out[job.role_family] = out.get(job.role_family, 0) + 1
        s.commit()
    return out


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    if ap.add_argument("--backfill", action="store_true") and ap.parse_args().backfill:
        print(backfill())
