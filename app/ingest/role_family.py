"""Rule-based role family (no LLM): engineering | data_ml | product | design | other.

1. Customer-facing "engineer" titles (sales/solutions/support/field engineer) are other.
2. "Content/training/course/curriculum/instructional developer" is other (content work, not software).
3. Hard non-tech words (sales, revenue, partnerships, content, risk management, verification, benefits,
   recruiting, people operations) make a title other unless its head names an engineer/developer/SDE/scientist.
4. Classify on the title HEAD - the part before " - ", "(", ":" or "," - so a team/product suffix such as
   "- AI Agents (Healthcare)" does not decide the role. If the head has no keyword, fall back to the full title.
5. Weak title words (ai, platform, systems, security, embedded, cloud, ...) are ignored when the job's extracted
   required skills contain nothing technical.
6. Generic titles (Intern, Associate, Graduate, Trainee) use the extracted required skills as a tie-break.
Per-job overrides reviewed by a human live in config (ingest.role_family_overrides) and win over the rules.
Matching, evals and drafting use only TECH_FAMILIES by default (via jobs.in_scope).

    python -m app.ingest.role_family --backfill
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.extract.vocab import SKILL_VOCAB

TECH_FAMILIES = ("engineering", "data_ml", "product")

_R = lambda p: re.compile(p, re.I)  # noqa: E731

CUSTOMER_FACING = _R(r"\b(sales|solutions?|pre-?sales|customer|support|field|implementation|technical account|"
                     r"account|partner|success|onboarding|deployment strategist|value)\b.*\b(engineer|consultant|"
                     r"architect|specialist|manager|executive)\b|\bsales engineer\b|\bsupport engineer\b|"
                     r"\bsolutions? (engineer|consultant)\b|\bcustomer engineer\b")
CONTENT_DEVELOPER = _R(r"\b(content|training|course|curriculum|instructional|learning)\s+(developer|development)\b")
HARD_NON_TECH = _R(r"\b(sales|revenue|partnerships?|content|risk management|verification|benefits?|recruit\w*|"
                   r"people operations)\b")
TECH_HEAD = _R(r"\b(engineer\w*|developer|sde|swe|scientist)\b")
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
WEAK_WORDS = {"ai", "ml", "platform", "systems", "security", "embedded", "cloud", "network", "infrastructure", "llm",
              "genai", "generative"}
_HEAD_SPLIT = re.compile(r"\s+[-–|]\s+|\s*\(|:|,")

PROGRAMMING = {"python", "java", "javascript", "typescript", "go", "rust", "c++", "c#", "kotlin", "swift", "scala",
               "react", "node.js", "django", "flask", "fastapi", "spring boot", "android", "ios", "kubernetes",
               "docker", "aws", "linux", "rest apis", "microservices", "data structures", "algorithms"}
DATA_SKILLS = {"machine learning", "deep learning", "pytorch", "tensorflow", "scikit-learn", "pandas", "numpy",
               "statistics", "llm", "natural language processing", "computer vision", "spark", "airflow", "etl",
               "data analysis", "power bi", "tableau", "sql"}
TECH_SKILLS = PROGRAMMING | DATA_SKILLS | set(SKILL_VOCAB)


def title_head(title: str) -> str:
    """The role part of a title: before ' - ', '(', ':' or ','."""
    return _HEAD_SPLIT.split(title or "", maxsplit=1)[0].strip()


def _keyword_family(t: str) -> tuple[str, str] | None:
    for fam, rx in (("design", DESIGN), ("product", PRODUCT), ("data_ml", DATA_ML), ("engineering", ENGINEERING)):
        m = rx.search(t)
        if m:
            return fam, m.group(0).lower()
    return None


def classify_role(title: str, required_skills: Iterable[str] | None = None) -> tuple[str, str]:
    """(family, reason)."""
    t = title or ""
    head = title_head(t) or t
    if CUSTOMER_FACING.search(t):
        return "other", "customer-facing title"
    if CONTENT_DEVELOPER.search(t):
        return "other", "content/training developer (not software)"
    m = HARD_NON_TECH.search(t)
    if m and not TECH_HEAD.search(head):
        return "other", f"non-tech word '{m.group(0).lower()}'"
    hit = _keyword_family(head)
    where = "title"
    if hit is None and head != t:
        hit, where = _keyword_family(t), "full title"
    if hit:
        fam, word = hit
        sk = {s.lower() for s in (required_skills or [])}
        if word in WEAK_WORDS and sk and not (sk & TECH_SKILLS):
            return "other", f"weak word '{word}' and no technical skill required"
        return fam, f"{where} has '{word}'"
    if GENERIC.search(t) and not NON_TECH.search(t):
        sk = {s.lower() for s in (required_skills or [])}
        if len(sk & DATA_SKILLS - {"sql"}) >= 2:
            return "data_ml", "generic title; data/ML skills required"
        if len(sk & PROGRAMMING) >= 2:
            return "engineering", "generic title; programming skills required"
    return "other", "no tech title keyword"


def role_for_job(job_key: str, title: str, required_skills: Iterable[str] | None = None) -> tuple[str, str]:
    """classify_role, unless a human-reviewed override exists for this job (ingest.role_family_overrides)."""
    from app.config import get_settings

    ov = get_settings().cfg.ingest.role_family_overrides.get(job_key)
    if ov:
        return ov["family"], f"override: {ov['reason']}"
    return classify_role(title, required_skills)


def backfill() -> dict[str, int]:
    from sqlalchemy import select

    from app.db import session_factory
    from app.models import Job, JobFactsRow

    out: dict[str, int] = {}
    with session_factory()() as s:
        for job, facts in s.execute(select(Job, JobFactsRow.facts).outerjoin(JobFactsRow, JobFactsRow.job_id == Job.id)):
            skills = ((facts or {}).get("required_skills") or {}).get("value") or []
            job.role_family, job.role_reason = role_for_job(f"{job.source}:{job.source_job_id}", job.title, skills)
            out[job.role_family] = out.get(job.role_family, 0) + 1
        s.commit()
    return out


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    if ap.add_argument("--backfill", action="store_true") and ap.parse_args().backfill:
        print(backfill())
