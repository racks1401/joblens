"""JobFacts extraction (spec 9.2): prompt -> chat_json -> validate (retry <=2) -> evidence check -> normalize -> store."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from pydantic import ValidationError
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.extract.prompting import build_messages
from app.extract.vocab import norm_degree, norm_location, norm_skill, norm_specialization
from app.llm.client import DailyLimitReached, LLMClient, LLMJSONError
from app.logs import log_event
from app.models import Job, JobFactsRow
from app.schemas import EVIDENCED_FIELDS, Evidenced, JobFacts, Money

log = logging.getLogger("joblens.extract")
MAX_RETRIES = 2
# Pasted-job title flags (the title text is the flag; the API exposes them as JobOut.flags).
TITLE_UNKNOWN = "Title unknown"
NEEDS_HUMAN_TITLE = "Needs human title"
LIST_FIELDS = {"locations", "degrees_allowed", "specializations", "graduation_years", "required_skills",
               "nice_to_have_skills"}
_ENUM_ALIASES = {
    "employment_type": {"intern": "internship", "full-time": "full_time", "fulltime": "full_time", "full time": "full_time",
                        "part-time": "part_time", "part time": "part_time", "contractor": "contract"},
    "work_mode": {"on-site": "onsite", "on site": "onsite", "office": "onsite", "in-office": "onsite",
                  "wfh": "remote", "work from home": "remote"},
}


def posting_text(title: str, company: str | None, location: str | None, description: str) -> str:
    """The exact text given to the extractor; evidence must be a substring of it.

    ATS location/company/title are metadata, not part of the description, so they are included as
    header lines so that facts like locations can be quoted.
    """
    head = [f"Title: {title}"]
    if company:
        head.append(f"Company: {company}")
    if location:
        head.append(f"Location: {location}")
    return "\n".join(head) + "\n\n" + description


def job_posting_text(job: Job, max_chars: int = 18000) -> str:
    """For pasted jobs the title is only a guess, so it is not shown to the extractor: the model reads the
    role from the posting itself (JobFacts.title), which then becomes the job title."""
    if job.source == "paste":
        return job.description_text[:max_chars]
    return posting_text(job.title, job.company or None, job.location_raw, job.description_text)[:max_chars]


@lru_cache
def facts_schema_json() -> str:
    return json.dumps(JobFacts.model_json_schema(), separators=(",", ":"))


_QUOTES = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
    "‐": "-", "‑": "-", "‒": "-", "−": "-",  # hyphen variants
    "­": "", " ": " ", "•": "-",
})
_WRAP = "\"'“”‘’`"


def norm_for_match(s: str) -> str:
    """Normalization for the evidence check: case, whitespace, typographic quotes and dash variants,
    and two artifacts of our own text conversion.

    - List-bullet markers at line starts ("- ", "* ") are dropped: our HTML-to-text step adds them.
    - Sentence punctuation followed by a space (". ", "; ", ", ") is dropped: once bullets are gone,
      models join list items with ". " or "; ", and our conversion can leave ". ." runs. Punctuation
      inside tokens ("7.0", "node.js", "B.Tech") is kept, so numbers and names are not changed.
    """
    s = re.sub(r"(?m)^\s*[-*]\s+", "", s.translate(_QUOTES))
    s = re.sub(r"[.,;:!?]+(?=\s|$)", "", s)
    return " ".join(s.lower().split())


def evidence_ok(evidence: str | None, text_norm: str) -> bool:
    """True if the evidence is verbatim in the (normalized) posting text.

    Wrapping quote marks are ignored. A quote stitched with "..." passes only if EVERY fragment is verbatim.
    """
    if not evidence:
        return False
    raw = evidence.strip().strip(_WRAP).strip()
    frags = [norm_for_match(f.strip().strip(_WRAP)) for f in re.split(r"\.\.\.+|…", raw)]
    frags = [f for f in frags if f]
    if not frags:
        return False
    if len(frags) == 1:
        return frags[0] in text_norm
    return all(len(f) >= 3 and f in text_norm for f in frags)


def _empty(v: Any) -> bool:
    return v is None or v == "" or v == [] or (isinstance(v, dict) and all(x is None for x in v.values()))


def coerce_raw(raw: dict[str, Any], title: str, company: str | None) -> dict[str, Any]:
    """Safe shape fixes before validation: wrap bare values, scalar->list, enum spellings, metadata."""
    out = dict(raw)
    for k in ("title", "company"):  # some models wrap these plain strings as {"value", "evidence"}
        if isinstance(out.get(k), dict):
            out[k] = out[k].get("value")
    out["title"] = out.get("title") or title
    if not out.get("company"):
        out["company"] = company or None
    for f in EVIDENCED_FIELDS:
        v = out.get(f)
        if v is None:
            out.pop(f, None)  # bare null ("duration_months": null) means "not stated" -> default empty field
            continue
        if not isinstance(v, dict) or ("value" not in v and "evidence" not in v):
            v = {"value": v, "evidence": None}
        v = dict(v)
        if f in LIST_FIELDS and isinstance(v.get("value"), str | int):
            v["value"] = [v["value"]]
        if f in _ENUM_ALIASES and isinstance(v.get("value"), str):
            val = v["value"].strip().lower()
            v["value"] = _ENUM_ALIASES[f].get(val, val.replace("-", "_").replace(" ", "_"))
        out[f] = v
    flags = out.get("red_flags") or []
    out["red_flags"] = [x if isinstance(x, dict) else {"value": str(x), "evidence": None} for x in flags]
    return out


def check_evidence(facts: JobFacts, text: str) -> tuple[JobFacts, list[str]]:
    """Null every non-null field whose evidence is not a substring of the text. Returns dropped field names."""
    tn = norm_for_match(text)
    dropped: list[str] = []
    for f in EVIDENCED_FIELDS:
        ev: Evidenced[Any] = getattr(facts, f)
        val = ev.value
        if _empty(val.model_dump() if isinstance(val, Money) else val):
            setattr(facts, f, type(ev)())
            continue
        if not evidence_ok(ev.evidence, tn):
            dropped.append(f)
            setattr(facts, f, type(ev)())
        elif ev.evidence and len(ev.evidence) > 200:
            ev.evidence = ev.evidence[:200]
    kept = []
    for rf in facts.red_flags:
        if rf.value and evidence_ok(rf.evidence, tn):
            kept.append(rf)
        else:
            dropped.append("red_flags")
    facts.red_flags = kept
    return facts, dropped


_LAKH = re.compile(r"(?i)\b(?:LPA|L\.P\.A\.?|lakhs?|lacs?)\b")
_LAKH_MONTH = re.compile(r"(?i)\b(?:lakhs?|lacs?)\b\s*(?:per|a|/)\s*(?:month|mo)\b")


def normalize_lakh_pay(facts: JobFacts) -> JobFacts:
    """Indian pay stated in lakhs ("15.65 LPA", "1.5 lakh a month") -> full INR amounts, in code.

    Only when the pay evidence quote itself says LPA/lakh and the numbers are lakh-sized (< 1000), so a
    figure already in rupees is never multiplied. The evidence quote is left unchanged.
    """
    m, ev = facts.pay.value, facts.pay.evidence or ""
    if m is None or not _LAKH.search(ev):
        return facts
    vals = [v for v in (m.min, m.max) if v is not None]
    if not vals or max(vals) >= 1000:
        return facts
    m.min = None if m.min is None else round(m.min * 100_000)
    m.max = None if m.max is None else round(m.max * 100_000)
    m.currency = "INR"
    m.period = "month" if _LAKH_MONTH.search(ev) else "year"
    return facts


_BATCH_DEADLINE = re.compile(
    r"(?i)\bobtain(?:ed)?\s+by\b|\bcomplet\w*\s+(?:(?:your|the|their|a)\s+)?(?:degree\s+|studies\s+|graduation\s+)?"
    r"(?:by|before)\b|\bno later than\b|\bby\s+(?:the\s+)?(?:summer|spring|fall|autumn|winter|end of)\b|"
    r"\b(?:prior to|before)\s+(?:the\s+)?start\b")
_BATCH_WORD = re.compile(r"(?i)\bbatch\b|\bgraduat\w*|\bclass of\b|\bpass[- ]?outs?\b|\bpassing out\b|\balumni\b")
_MONTH = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
          r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_JOB_DATE = re.compile(r"(?i)\b(?:join\w*|start\w*|internship|tenure|duration|commenc\w*|onboard\w*)\b|"
                       r"\b" + _MONTH + r"\.?\s+(?:\d{1,2},?\s+)?20\d\d\b|\b" + _MONTH + r"\s+(?:to|-|–)\s+" + _MONTH + r"\b")


def guard_batch_years(facts: JobFacts) -> tuple[JobFacts, str | None]:
    """Clear graduation_years when its quote is not a batch list (no LLM):
    - degree-completion deadline wording ("obtained by summer 2027", "complete your degree before ...",
      "no later than", "by summer"): anyone graduating by then qualifies, which a year list cannot express;
    - a job/internship date ("January to June 2027", "Joining: June 2027") with no batch word.
    Returns (facts, reason or None)."""
    g = facts.graduation_years
    if not g.value or not g.evidence:
        return facts, None
    ev = g.evidence
    reason = None
    if _BATCH_DEADLINE.search(ev):
        reason = "degree-completion deadline, not a batch"
    elif _JOB_DATE.search(ev) and not _BATCH_WORD.search(ev):
        reason = "job/internship date, not a batch"
    if reason:
        log_event(log, "batch_years_cleared", level=logging.WARNING, value=g.value, evidence=ev[:120], reason=reason)
        facts.graduation_years = Evidenced()
    return facts, reason


def normalize_facts(facts: JobFacts) -> JobFacts:
    """Skills via synonym map, degree codes, title-cased locations, normalized specializations."""
    def uniq(xs: list[Any]) -> list[Any]:
        return list(dict.fromkeys(x for x in xs if x not in (None, "")))

    if facts.required_skills.value:
        facts.required_skills.value = uniq([norm_skill(s) for s in facts.required_skills.value])
    if facts.nice_to_have_skills.value:
        req = set(facts.required_skills.value or [])
        facts.nice_to_have_skills.value = [s for s in uniq([norm_skill(x) for x in facts.nice_to_have_skills.value])
                                           if s not in req]
    if facts.degrees_allowed.value:
        facts.degrees_allowed.value = uniq([norm_degree(d) for d in facts.degrees_allowed.value])
    if facts.locations.value:
        facts.locations.value = uniq([norm_location(x) for x in facts.locations.value
                                      if not re.fullmatch(r"(?i)\s*remote\s*", x)]) or None
        if facts.locations.value is None:
            facts.locations = Evidenced()
    if facts.specializations.value:
        facts.specializations.value = uniq([norm_specialization(x) for x in facts.specializations.value])
    if facts.graduation_years.value:
        facts.graduation_years.value = sorted(set(facts.graduation_years.value))
    if facts.pay.value and facts.pay.value.currency:
        facts.pay.value.currency = facts.pay.value.currency.upper().replace("RS", "INR").replace("₹", "INR")
    return facts


@dataclass
class ExtractionResult:
    facts: JobFacts
    valid: bool
    model: str
    prompt_version: str
    attempts: int
    dropped: list[str] = field(default_factory=list)
    error: str | None = None
    tokens: int = 0


def extract_facts(text: str, title: str, company: str | None, llm: LLMClient,
                  prompt_version: str = "facts_v1", model: str | None = None) -> ExtractionResult:
    """Run extraction with up to MAX_RETRIES retries that append the validation error."""
    messages: list[dict[str, Any]] = build_messages(prompt_version, json_schema=facts_schema_json(), job_text=text)
    error: str | None = None
    tokens = 0
    used_model = model or llm.model
    for attempt in range(1, MAX_RETRIES + 2):
        last_content = ""
        try:
            raw, res = llm.chat_json(messages, JobFacts, model=model, temperature=0.0, prompt_version=prompt_version)
            tokens += res.tokens_in + res.tokens_out
            used_model = res.model or used_model
            last_content = res.content or ""
            facts = JobFacts.model_validate(coerce_raw(raw, title, company))
        except (LLMJSONError, ValidationError) as e:
            error = str(e)[:1500]
            log_event(log, "facts_invalid", level=logging.WARNING, attempt=attempt, error=error[:300])
            messages = messages + [
                {"role": "assistant", "content": last_content},
                {"role": "user", "content": f"Your previous output failed validation:\n{error}\nReturn corrected JSON only."},
            ]
            continue
        facts, dropped = check_evidence(facts, text)
        for f in dropped:
            log_event(log, "evidence_missing", level=logging.WARNING, field=f, prompt_version=prompt_version)
        return ExtractionResult(normalize_facts(facts), True, used_model, prompt_version, attempt, dropped, None, tokens)
    return ExtractionResult(JobFacts(title=title, company=company or None), False, used_model, prompt_version,
                            MAX_RETRIES + 1, [], error, tokens)


def store_facts(s: Session, job_id: int, r: ExtractionResult) -> None:
    data = r.facts.model_dump(mode="json")
    stmt = insert(JobFactsRow).values(job_id=job_id, facts=data, model=r.model, prompt_version=r.prompt_version,
                                      valid=r.valid, extracted_at=datetime.now(UTC))
    s.execute(stmt.on_conflict_do_update(index_elements=["job_id"], set_={
        "facts": data, "model": r.model, "prompt_version": r.prompt_version, "valid": r.valid,
        "extracted_at": datetime.now(UTC)}))


def process_jobs(s: Session, job_ids: Sequence[int], llm: LLMClient | None = None,
                 prompt_version: str | None = None, **_: object) -> dict[str, int]:
    """Pipeline step: extract and store facts for the given jobs."""
    from app.config import get_settings
    from app.llm.client import get_llm

    st = get_settings()
    llm = llm or get_llm("extract")
    pv = prompt_version or st.cfg.prompts.facts
    max_chars = min(st.cfg.llm.extract_max_chars, max(2000, (st.cfg.llm.max_input_tokens - 1500) * 4))
    out = {"valid": 0, "invalid": 0, "evidence_dropped": 0}
    for jid in job_ids:
        job = s.get(Job, jid)
        if job is None:
            continue
        try:
            jpv = prompt_version or (st.cfg.prompts.facts_paste or pv if job.source == "paste" else pv)
            r = extract_facts(job_posting_text(job, max_chars), job.title, job.company or None, llm, jpv)
        except DailyLimitReached:
            raise
        except Exception as e:  # noqa: BLE001 - one job's provider error must not stop the run
            log_event(log, "facts_job_failed", level=logging.WARNING, job_id=jid, error=f"{type(e).__name__}: {str(e)[:200]}")
            out["errors"] = out.get("errors", 0) + 1
            continue  # facts left as they were, so `--stale` retries this job next time
        if job.source == "paste":
            r.facts = normalize_lakh_pay(r.facts)
        r.facts, _batch_reason = guard_batch_years(r.facts)
        store_facts(s, jid, r)
        from app.ingest.role_family import classify_role
        from app.ingest.seniority import classify

        # Split pieces ("role<N>-..." ids) keep the splitter's title: the heading line or "Needs human title".
        if job.source == "paste" and not job.source_job_id.startswith("role") and job.title != NEEDS_HUMAN_TITLE:
            # Accept the extracted role only if it is written in the posting; never invent a title.
            t = (r.facts.title or "").strip() if r.valid else ""
            job.title = t[:200] if t and norm_for_match(t) in norm_for_match(job.description_text) else TITLE_UNKNOWN
            job.is_fresher_relevant, job.seniority_reason = classify(job.title, job.description_text)
        job.role_family, job.role_reason = classify_role(job.title, r.facts.required_skills.value)
        from app.ingest.seniority import apply_extracted_experience

        apply_extracted_experience(job, r.facts.model_dump(mode="json"))
        s.commit()
        out["valid" if r.valid else "invalid"] += 1
        out["evidence_dropped"] += len(r.dropped)
    return out
