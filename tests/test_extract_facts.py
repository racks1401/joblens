import json
from typing import Any

import pytest

from app.extract import heuristic
from app.extract.facts import (
    check_evidence,
    coerce_raw,
    evidence_ok,
    extract_facts,
    norm_for_match,
    normalize_facts,
    posting_text,
)
from app.llm.cache import MemoryCache
from app.llm.client import LLMClient, LLMResult
from app.llm.fake import FakeProvider
from app.schemas import Evidenced, JobFacts, Money

POSTING = posting_text("Python Developer Intern", "Acme Labs", None, """We are hiring interns for our Bengaluru office (hybrid).
Duration: 6 months internship with PPO opportunity.
Stipend: Rs 25,000 per month.
Eligibility: B.Tech / MCA students, 2026 and 2027 batch, minimum 7.0 CGPA, all branches.
Requirements:
- Python, SQL and REST APIs
- Basic Git
Nice to have:
- Docker, Kubernetes
Last date to apply: 30 November 2026. Freshers can apply.""")

SCAM = posting_text("Work From Home Data Entry", "", None, """Earn Rs 5000 per day from home! No interview.
Pay a registration fee of Rs 999 to confirm your seat. Contact us on WhatsApp only: +91 90000 00000.""")


def fake_llm(overrides: dict[str, Any] | None = None) -> LLMClient:
    return LLMClient(FakeProvider(overrides), "fake-heuristic", MemoryCache())


def test_heuristic_extracts_indian_internship() -> None:
    r = extract_facts(POSTING, "Python Developer Intern", "Acme Labs", fake_llm())
    f = r.facts
    assert r.valid and r.dropped == [] and r.model == "fake-heuristic"
    assert f.employment_type.value == "internship" and f.work_mode.value == "hybrid"
    assert f.locations.value == ["Bengaluru"] and f.duration_months.value == 6
    assert f.pay.value == Money(min=25000, max=25000, currency="INR", period="month")
    assert f.degrees_allowed.value == ["BTECH", "MCA"] and f.specializations.value == ["all"]
    assert f.graduation_years.value == [2026, 2027] and f.min_cgpa.value == 7.0
    assert f.fresher_friendly.value is True and f.ppo_or_conversion.value is True
    assert f.required_skills.value == ["python", "sql", "rest apis", "git"]
    assert f.nice_to_have_skills.value == ["docker", "kubernetes"]
    assert str(f.apply_deadline.value) == "2026-11-30"
    # every evidence is a verbatim substring
    for name in ["employment_type", "pay", "degrees_allowed", "graduation_years", "min_cgpa", "required_skills"]:
        assert getattr(f, name).evidence in POSTING


def test_heuristic_red_flags() -> None:
    flags = heuristic.red_flags(SCAM)
    assert {f["value"] for f in flags} == {"asks for a fee or deposit", "contact only via WhatsApp/Telegram",
                                           "promises guaranteed income"}
    assert heuristic.red_flags(POSTING) == []


@pytest.mark.parametrize("text,expected", [
    ("Salary: 6-8 LPA", {"min": 600000, "max": 800000, "currency": "INR", "period": "year"}),
    ("Stipend ₹15k/month", {"min": 15000, "max": 15000, "currency": "INR", "period": "month"}),
    ("$120,000 - $150,000 per year", {"min": 120000, "max": 150000, "currency": "USD", "period": "year"}),
    ("Compensation: $150K – $200K", {"min": 150000, "max": 200000, "currency": "USD", "period": None}),
])
def test_heuristic_pay_patterns(text: str, expected: dict[str, Any]) -> None:
    assert heuristic.pay(text)["value"] == expected


def test_heuristic_unknown_is_null() -> None:
    f = heuristic.extract_facts("Title: Engineer\n\nWe build things.")
    assert f["min_cgpa"] == {"value": None, "evidence": None}
    assert f["graduation_years"]["value"] is None and f["pay"]["value"] is None


def test_evidence_check_drops_unsupported_values() -> None:
    text = "Title: X\n\nMust have 7.5 CGPA. Python required."
    f = JobFacts(title="X", min_cgpa=Evidenced(value=7.5, evidence="must  have 7.5 cgpa"),
                 required_skills=Evidenced(value=["python", "java"], evidence="Java required"),
                 duration_months=Evidenced(value=6, evidence=None),
                 red_flags=[Evidenced(value="fee", evidence="pay a fee")])
    f2, dropped = check_evidence(f, text)
    assert f2.min_cgpa.value == 7.5  # whitespace/case-insensitive match
    assert f2.required_skills.value is None and f2.duration_months.value is None and f2.red_flags == []
    assert sorted(dropped) == ["duration_months", "red_flags", "required_skills"]


def test_evidence_normalization() -> None:
    assert norm_for_match("It’s  Great\n") == "it's great"
    assert evidence_ok("“Remote” role...", norm_for_match('A "remote" role here'))
    assert not evidence_ok("", "anything")


def test_coerce_and_normalize() -> None:
    raw = {"employment_type": {"value": "Full-time", "evidence": "x"}, "locations": {"value": "bangalore", "evidence": "x"},
           "required_skills": ["JS", "Postgres", "js"], "degrees_allowed": {"value": ["B.Tech", "M.C.A"], "evidence": "x"},
           "red_flags": ["whatsapp"]}
    f = JobFacts.model_validate(coerce_raw(raw, "T", "C"))
    assert f.title == "T" and f.company == "C" and f.employment_type.value == "full_time"
    assert f.required_skills.evidence is None  # bare value wrapped without evidence -> dropped later
    f.required_skills.evidence = "x"
    f = normalize_facts(f)
    assert f.locations.value == ["Bengaluru"] and f.required_skills.value == ["javascript", "postgresql"]
    assert f.degrees_allowed.value == ["BTECH", "MCA"]
    from app.extract.vocab import norm_degree

    assert norm_degree("Diploma") == "OTHER" and norm_degree("bachelor's") == "BACHELORS"


def test_retry_appends_validation_error_then_succeeds() -> None:
    calls: list[list[dict[str, Any]]] = []

    def flaky(messages: list[dict[str, Any]], tools: Any) -> LLMResult:
        calls.append(messages)
        if len(calls) == 1:
            return LLMResult(content=json.dumps({"title": "T", "min_cgpa": {"value": "high", "evidence": "x"}}))
        return LLMResult(content=json.dumps({"title": "T", "min_cgpa": {"value": 7, "evidence": "7 CGPA"}}))

    r = extract_facts("Title: T\n\n7 CGPA needed", "T", None, fake_llm({"facts": flaky}))
    assert r.valid and r.attempts == 2 and r.facts.min_cgpa.value == 7
    assert "failed validation" in calls[1][-1]["content"]


def test_invalid_after_retries_marks_invalid() -> None:
    r = extract_facts("Title: T\n\nx", "T", "C", fake_llm({"facts": lambda m, t: LLMResult(content="not json")}))
    assert not r.valid and r.attempts == 3 and r.facts.title == "T" and r.error


@pytest.mark.parametrize("text,flagged", [
    ("We'll also never ask you to pay a fee, send money or deposit a check.", False),
    ("Beware of fraudsters asking for a registration fee.", False),
    ("Pay a registration fee of Rs 500 to confirm.", True),
    ("powering businesses like Airbnb, Facebook, WhatsApp, Airtel", False),
    ("Send your CV on WhatsApp to 9876543210", True),
])
def test_red_flag_context(text: str, flagged: bool) -> None:
    assert bool(heuristic.red_flags(text)) is flagged


def test_stitched_evidence_needs_every_fragment_verbatim() -> None:
    tn = norm_for_match("Ownership comes naturally to you. You have excessive curiosity about AI.")
    assert evidence_ok("Ownership comes naturally to you... excessive curiosity", tn)
    assert not evidence_ok("Ownership comes naturally to you... invented requirement", tn)
    assert not evidence_ok("... ...", tn)


def test_bullet_markers_ignored_in_evidence_match() -> None:
    tn = norm_for_match("Requirements:\n- Python and SQL.\n- Docker experience.")
    assert evidence_ok("Python and SQL. Docker experience.", tn)
    assert evidence_ok("- Python and SQL.", tn)


@pytest.mark.parametrize("evidence,ok", [
    ("hands‑on experience with Python", True),        # non-breaking hyphen vs "-"
    ('"Coinbase is a remote-first company"', True),         # wrapping quote marks
    ("data dashboards; highly proficient in SQL", True),    # punctuation where a bullet line break was
    ("pipelines. exposure to APIs", True),                  # our ". ." artifact
    ("minimum 70 CGPA", False),                             # "7.0" is not "70": in-token punctuation kept
    ("Job is a senior leadership role", False),             # invented
    ("10+ years of overall experience, 5+ years leading", False),  # skipped "and"
])
def test_evidence_formatting_vs_real_mismatch(evidence: str, ok: bool) -> None:
    text = ("Title: X\n\nCoinbase is a remote-first company.\n- Hands-on experience with Python\n- Build data dashboards\n"
            "- Highly proficient in SQL\nData pipelines. . Exposure to APIs.\nMinimum 7.0 CGPA.\n"
            "10+ years of overall experience, and 5+ years leading teams.")
    assert evidence_ok(evidence, norm_for_match(text)) is ok


def test_coerce_unwraps_evidenced_title_company() -> None:
    raw = {"title": {"value": "SDE Intern", "evidence": "Title: SDE Intern"}, "company": {"value": "Paytm", "evidence": "x"}}
    f = JobFacts.model_validate(coerce_raw(raw, "fallback", None))
    assert f.title == "SDE Intern" and f.company == "Paytm"


def test_coerce_bare_null_field() -> None:
    f = JobFacts.model_validate(coerce_raw({"title": "T", "duration_months": None, "ppo_or_conversion": None}, "T", None))
    assert f.duration_months.value is None and f.ppo_or_conversion.value is None


@pytest.mark.db
def test_paste_title_comes_from_extracted_role(session) -> None:  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime

    from app.extract.facts import job_posting_text, process_jobs
    from app.ingest.paste import from_paste
    from app.ingest.store import upsert_job
    from app.models import Job

    row, _ = upsert_job(session, from_paste("Company: Acme\n\nWe are hiring a Backend Engineer Intern.\nPython and SQL needed."),
                        datetime.now(UTC))
    session.commit()
    assert not job_posting_text(row).startswith("Title:")  # the guess is not shown to the extractor
    llm = fake_llm({"facts": lambda m, t: LLMResult(content=json.dumps({"title": "Backend Engineer Intern"}))})
    process_jobs(session, [row.id], llm=llm)
    job = session.get(Job, row.id)
    assert job.title == "Backend Engineer Intern" and job.company == "Acme" and job.role_family == "engineering"


def test_facts_v3_is_v1_plus_two_rules() -> None:
    from app.extract.prompting import build_messages, load_prompt

    v1, v3 = load_prompt("facts_v1")[0], load_prompt("facts_v3")[0]
    assert "NOT batch years" in v3 and '["all"]' in v3 and "NOT batch years" not in v1
    msgs = build_messages("facts_v3", json_schema="{}", job_text="POSTING")
    assert "POSTING" in msgs[1]["content"]


@pytest.mark.db
def test_paste_title_must_be_in_posting_and_paste_stays_in_scope(session) -> None:  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime

    from app.extract.facts import process_jobs
    from app.ingest.paste import from_paste
    from app.ingest.store import upsert_job
    from app.models import Job

    row, _ = upsert_job(session, from_paste("Company: Acme\n\nWHO CAN APPLY\n* Degrees: B.Tech\n* Stipend: INR 20,000"),
                        datetime.now(UTC))
    session.commit()
    llm = fake_llm({"facts": lambda m, t: LLMResult(content=json.dumps({"title": "Acme Hiring Drive"}))})  # invented
    process_jobs(session, [row.id], llm=llm)
    session.expire_all()
    job = session.get(Job, row.id)
    assert job.title == "Title unknown" and job.role_family == "other"
    assert job.in_scope is True  # pasted jobs skip the role-family filter
