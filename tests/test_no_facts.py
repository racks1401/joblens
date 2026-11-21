"""Jobs without usable extracted facts (no job_facts row yet, or extraction failed) are never judged as if the
posting states nothing: every rule needs checking, the verdict is CHECK, Matches and search leave them out, and the
agent's tools say "requirements not extracted yet"."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.eligibility import NOT_EXTRACTED, RULE_NAMES, check_eligibility, usable_facts
from app.schemas import Evidenced, JobFacts, Profile

PROFILE = {"degree": "MCA", "specialization": "AI", "graduation_year": 2027, "cgpa": 7.9, "experience_years": 0,
           "skills": [{"name": "python"}, {"name": "sql"}], "target_roles": ["data analyst"],
           "preferred_locations": ["Bengaluru"]}
JOBS = [
    ("Role: Data Analyst Intern\nCompany: Alpha Analytics\nLocation: Bengaluru\nStipend: Rs 15,000 per month.\n"
     "Skills: Python, SQL.\nEligibility: MCA graduates of 2027 batch."),
    ("Role: Data Analyst Trainee\nCompany: Beta Analytics\nLocation: Bengaluru\nStipend: Rs 18,000 per month.\n"
     "Skills: Python, SQL.\nEligibility: MCA graduates of 2027 batch."),
    ("Role: Data Analyst Graduate\nCompany: Gamma Analytics\nLocation: Bengaluru\nStipend: Rs 20,000 per month.\n"
     "Skills: Python, SQL.\nEligibility: MCA graduates of 2027 batch."),
]


# --- eligibility engine


def test_no_facts_is_check_with_every_rule_unknown() -> None:
    e = check_eligibility(Profile.model_validate(PROFILE), None)
    assert e.verdict == "CHECK" and e.facts_extracted is False
    assert [r.rule for r in e.rules] == RULE_NAMES
    assert all(r.status == "UNKNOWN" and NOT_EXTRACTED in r.message and r.evidence is None for r in e.rules)


def test_a_stated_minimum_still_fails_and_extracted_facts_are_marked() -> None:
    """The reported case: '2 - 5 years' extracted as 2 must fail a fresher; it only passed when facts were missing."""
    f = JobFacts(title="Software Engineer - Database",
                 experience_min_years=Evidenced[float](value=2.0, evidence="2 - 5 years of hands-on experience"))
    e = check_eligibility(Profile.model_validate(PROFILE), f)
    exp = next(r for r in e.rules if r.rule == "experience")
    assert exp.status == "FAIL" and e.verdict == "INELIGIBLE" and e.facts_extracted is True


@pytest.mark.parametrize(("facts", "valid", "usable"), [
    (None, None, False),                      # no job_facts row yet
    ({"title": "x"}, False, False),           # extraction failed validation: stored as an empty JobFacts
    ({"title": "x"}, True, True),
])
def test_usable_facts(facts: dict[str, Any] | None, valid: bool | None, usable: bool) -> None:
    assert (usable_facts(facts, valid) is not None) is usable


# --- API, search and agent tools on the test DB


@pytest.fixture
def setup(sf):  # type: ignore[no-untyped-def]
    """Three in-scope board jobs: one with facts, one with no facts row, one whose extraction failed."""
    from sqlalchemy import text

    from app.db import get_engine
    from app.main import create_app
    from app.models import ProfileRow

    get_engine.cache_clear()
    c = TestClient(create_app())
    ids = []
    for i, t in enumerate(JOBS):
        r = c.post("/jobs/paste", json={"text": t, "url": f"https://jobs.example.test/{i}"})
        assert r.status_code == 200, r.text
        ids.append(r.json()["id"])
    ok, missing, failed = ids
    with sf() as s:
        s.execute(text("UPDATE jobs SET source = 'lever'"))  # board jobs: in scope only through the filters
        s.execute(text("DELETE FROM job_facts WHERE job_id = :j"), {"j": missing})
        s.execute(text("UPDATE job_facts SET valid = false, facts = jsonb_build_object('title', 'x') "
                       "WHERE job_id = :j"), {"j": failed})
        p = Profile.model_validate(PROFILE)
        row = ProfileRow(data=p.model_dump(mode="json"))
        s.add(row)
        s.commit()
        assert s.execute(text("SELECT count(*) FROM jobs WHERE in_scope")).scalar() == 3
        pid = row.id
    return c, pid, p, ok, missing, failed


@pytest.mark.db
def test_matches_and_search_leave_out_jobs_without_facts(setup) -> None:  # type: ignore[no-untyped-def]
    c, pid, _, ok, missing, failed = setup
    got = [x["job_id"] for x in c.get(f"/matches/{pid}", params={"show_ineligible": True}).json()["results"]]
    assert ok in got and missing not in got and failed not in got
    for mode in ("keyword", "vector", "hybrid"):
        for extra in ({}, {"include_out_of_scope": True}):
            r = c.get("/search", params={"q": "data analyst", "mode": mode, "profile_id": str(pid), **extra})
            got = [x["job_id"] for x in r.json()["results"]]
            assert ok in got and missing not in got and failed not in got, (mode, extra)


@pytest.mark.db
def test_job_detail_without_facts_is_check(setup) -> None:  # type: ignore[no-untyped-def]
    c, pid, _, ok, missing, failed = setup
    for jid in (missing, failed):
        e = c.get(f"/jobs/{jid}", params={"profile_id": str(pid)}).json()["eligibility"]
        assert e["verdict"] == "CHECK" and e["facts_extracted"] is False
        assert {r["status"] for r in e["rules"]} == {"UNKNOWN"}
        exp = next(r for r in e["rules"] if r["rule"] == "experience")
        assert "no minimum" not in exp["message"] and NOT_EXTRACTED in exp["message"]
    assert c.get(f"/jobs/{ok}", params={"profile_id": str(pid)}).json()["eligibility"]["facts_extracted"] is True


@pytest.mark.db
def test_agent_tools_say_requirements_not_extracted(setup, session) -> None:  # type: ignore[no-untyped-def]
    from app.agent.tools import (
        CompareJobsArgs,
        JobIdArgs,
        SearchJobsArgs,
        ToolContext,
        check_job_eligibility,
        compare_jobs,
        explain,
        get_job,
        search_jobs,
    )

    c, pid, p, ok, missing, _ = setup

    class NoLLM:
        model = "none"

        def chat_json(self, *a: Any, **k: Any) -> Any:
            raise AssertionError("explain must not call the LLM for a job without facts")

    ctx = ToolContext(session=session, profile_id=pid, profile=p, memory={}, explain_llm=NoLLM())
    e = check_job_eligibility(ctx, JobIdArgs(job_id=missing))
    assert e["verdict"] == "CHECK" and e["requirements"] == NOT_EXTRACTED and e["failed"] == []
    assert all(r["status"] == "UNKNOWN" for r in e["rules"])
    ex = explain(ctx, JobIdArgs(job_id=missing))
    assert ex["explanation_skipped"] is True and ex["eligibility"] == "CHECK" and ex["requirements"] == NOT_EXTRACTED
    card = get_job(ctx, JobIdArgs(job_id=missing))
    assert card["requirements"] == NOT_EXTRACTED and "experience_min_years" not in card and "pay" not in card
    rows = compare_jobs(ctx, CompareJobsArgs(job_ids=[ok, missing]))["jobs"]
    by_id = {r["job_id"]: r for r in rows}
    assert by_id[missing]["eligibility"] == "CHECK" and by_id[missing]["requirements"] == NOT_EXTRACTED
    assert "requirements" not in by_id[ok]
    found = search_jobs(ctx, SearchJobsArgs(query="Beta Analytics data analyst"))
    ids = [r["job_id"] for r in found["results"]] + [r["job_id"] for r in found["name_matches"] or []]
    assert missing not in ids
    # the explain route a visitor clicks goes through the same tool
    r = c.get(f"/matches/{pid}/{missing}/explain").json()
    assert r["explanation_skipped"] is True and r["eligibility"] == "CHECK"
