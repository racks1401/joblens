"""P6 agent: tools, grounding check, loop limits, memory and chat routes (fake LLM, offline)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.agent.tools import RememberArgs, SearchJobsArgs, ToolContext, execute_tool, tool_schemas
from app.agent.validate import check_answer
from app.llm.client import DailyLimitReached, LLMClient, LLMResult, MemoryCache, ToolCall

OUT = [{"results": [{"job_id": 1303, "title": "TLDP (Technology Leadership Development Program)",
                     "company": "Johnson & Johnson", "final_score": 0.61, "eligibility": "CHECK"}]},
       {"job_id": 1303, "verdict": "CHECK", "pay": {"min": 25000, "max": 30000, "currency": "INR", "period": "month",
                                                     "approx_inr_per_month": 27500},
        "min_cgpa": 7.0, "experience_min_years": 0, "apply_deadline": "2026-12-01",
        "rules": [{"rule": "cgpa", "status": "PASS", "message": "CGPA 7.96 meets 7.0"}]}]


# ---------- grounding check ----------

def test_grounded_answer_passes() -> None:
    ans = ("TLDP (Technology Leadership Development Program), Johnson & Johnson (job_id: 1303): eligibility CHECK.\n"
           "1. Pay ₹25,000 to ₹30,000 per month, min CGPA 7.0 (yours 7.96), apply by 2026-12-01.")
    g = check_answer(ans, OUT)
    assert g.ok, g.to_dict()
    assert 1303 in g.job_ids_checked and "7.0" in g.numbers_checked


def test_invented_id_and_numbers_flagged() -> None:
    g = check_answer("Try Data Intern, Acme (job_id: 4242). Stipend ₹40k/month, needs CGPA 8.5 and 2 years.", OUT)
    assert not g.ok
    assert g.unsupported_job_ids == [4242]
    vals = {u["value"] for u in g.unsupported_numbers}
    assert {"₹40k", "8.5"} <= vals
    assert any(u["kind"] == "years" for u in g.unsupported_numbers)


def test_rounded_pay_and_k_suffix_ok_but_wrong_date_flagged() -> None:
    g = check_answer("About ₹27.5k a month (job_id: 1303). Deadline 2026-12-15.", OUT)
    assert [u["value"] for u in g.unsupported_numbers] == ["2026-12-15"]


def test_job_named_without_id_flagged() -> None:
    g = check_answer("TLDP (Technology Leadership Development Program) looks good for you.", OUT)
    assert not g.ok and g.jobs_named_without_id[0]["job_id"] == 1303


def test_list_numbers_and_small_ints_skipped() -> None:
    g = check_answer("Here are 3 options:\n1. Job A (job_id: 1303)\n2) none else", OUT)
    assert g.ok and g.skipped_small_numbers == 1


# ---------- tools without a DB ----------

def test_tool_schemas_generated_from_pydantic() -> None:
    names = {t["function"]["name"] for t in tool_schemas()}
    assert names == {"search_jobs", "get_job", "check_eligibility", "explain_match", "compare_jobs", "remember"}
    sj = next(t for t in tool_schemas() if t["function"]["name"] == "search_jobs")["function"]["parameters"]
    assert "query" in sj["required"] and sj["properties"]["limit"]["maximum"] == 10
    assert SearchJobsArgs.model_json_schema() and RememberArgs.model_json_schema()


def test_invalid_args_return_error_not_raise() -> None:
    ctx = ToolContext(session=None, profile_id=None, profile=None, memory={})  # type: ignore[arg-type]
    r = execute_tool("compare_jobs", {"job_ids": [1]}, ctx)
    assert "invalid arguments" in r.output["error"]
    assert "unknown tool" in execute_tool("book_flight", {}, ctx).output["error"]
    assert "invalid arguments" in execute_tool("remember", {"key": "salary", "value": 1}, ctx).output["error"]


# ---------- DB: routes and loop ----------

PASTE = ("Role: Data Analyst Intern\nCompany: Example Analytics\nLocation: Bengaluru\n"
         "Stipend: Rs 15,000 per month. Duration: 6 months internship.\nSkills: Python, SQL, Excel, Power BI.\n"
         "Eligibility: B.Tech / BCA / B.Sc / MCA graduates of 2026 and 2027 batch. Freshers can apply.")
PROFILE = {"degree": "MCA", "specialization": "AI", "graduation_year": 2027, "cgpa": 7.9,
           "skills": [{"name": "python"}, {"name": "sql"}], "target_roles": ["data analyst"]}


@pytest.fixture
def client(sf):  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from app.db import get_engine
    from app.main import create_app

    get_engine.cache_clear()
    return TestClient(create_app())


@pytest.mark.db
def test_chat_search_eligibility_remember_and_memory_routes(client) -> None:  # type: ignore[no-untyped-def]
    job = client.post("/jobs/paste", json={"text": PASTE}).json()
    pid = client.post("/profiles", json=PROFILE).json()["id"]

    r = client.post("/chat", json={"profile_id": pid, "message": "data analyst internships"}).json()
    assert r["tool_trace"][0]["tool"] == "search_jobs"
    assert f"job_id: {job['id']}" in r["answer"] and r["grounding"]["ok"], r
    assert r["tokens"]["total"] > 0 and r["stop"] is None

    r2 = client.post("/chat", json={"profile_id": pid, "session_id": r["session_id"],
                                    "message": f"am I eligible for job {job['id']}"}).json()
    assert r2["session_id"] == r["session_id"]
    assert r2["tool_trace"][0]["tool"] == "check_eligibility"
    assert r2["tool_trace"][0]["output"]["verdict"] in ("ELIGIBLE", "CHECK", "INELIGIBLE")

    r3 = client.post("/chat", json={"profile_id": pid, "message": "remember that I prefer Bengaluru"}).json()
    assert r3["tool_trace"][0]["tool"] == "remember"
    assert client.get(f"/memory/{pid}").json()["memory"] == {"preferred_locations": ["Bengaluru"]}
    assert client.delete(f"/memory/{pid}/preferred_locations").json()["deleted"] == 1
    assert client.delete(f"/memory/{pid}/nope").status_code == 404

    ex = client.get(f"/matches/{pid}/{job['id']}/explain").json()
    assert ex["eligibility"] and "python" in ex["matched_skills"]
    assert client.get(f"/matches/{pid}/{job['id']}/explain").json().get("cached") is True


@pytest.mark.db
def test_search_name_matches_and_check_rules(client, session) -> None:  # type: ignore[no-untyped-def]
    from app.agent.tools import JobIdArgs, check_job_eligibility, search_jobs

    job = client.post("/jobs/paste", json={"text": PASTE}).json()
    pid, p = _profile(session)
    ctx = ToolContext(session=session, profile_id=pid, profile=p, memory={})
    out = search_jobs(ctx, SearchJobsArgs(query="zzz Example"))
    ids = [r["job_id"] for r in out["results"]] + [r["job_id"] for r in out["name_matches"] or []]
    assert job["id"] in ids
    assert search_jobs(ToolContext(session=session, profile_id=pid, profile=p,
                                   memory={"avoid_companies": ["example analytics"]}),
                       SearchJobsArgs(query="zzz Example"))["name_matches"] is None
    e = check_job_eligibility(ctx, JobIdArgs(job_id=job["id"]))
    assert e["verdict"] in ("ELIGIBLE", "CHECK", "INELIGIBLE") and "needs_checking" in e


class _Scripted:
    """Provider returning scripted results; raises if the script item is an exception."""

    name = "scripted"

    def __init__(self, items: list[Any]) -> None:
        self.items = items

    def complete(self, **kw: Any) -> LLMResult:
        it = self.items.pop(0)
        if isinstance(it, Exception):
            raise it
        return it


def _profile(session):  # type: ignore[no-untyped-def]
    from app.models import ProfileRow
    from app.schemas import Profile

    p = Profile.model_validate(PROFILE)
    row = ProfileRow(data=p.model_dump())
    session.add(row)
    session.commit()
    return row.id, p


@pytest.mark.db
def test_daily_limit_stops_cleanly(session) -> None:  # type: ignore[no-untyped-def]
    from app.agent.loop import run_turn

    pid, p = _profile(session)
    llm = LLMClient(_Scripted([DailyLimitReached("tokens per day")]), "m", MemoryCache())
    r = run_turn(session, pid, p, "hello", llm=llm)
    assert r["stop"].startswith("llm_limit: DailyLimitReached") and "could not finish" in r["answer"]
    assert r["grounding"]["ok"]


@pytest.mark.db
def test_max_steps_and_bad_args_go_back_to_model(session) -> None:  # type: ignore[no-untyped-def]
    from app.agent.loop import run_turn

    pid, p = _profile(session)
    bad = LLMResult(content=None, tool_calls=[ToolCall(id="1", name="get_job", arguments=json.dumps({"job_id": "x"}))])
    llm = LLMClient(_Scripted([bad] * 6), "m", MemoryCache())
    r = run_turn(session, pid, p, "loop forever", llm=llm)
    assert r["stop"].startswith("max_steps") and len(r["tool_trace"]) == 6
    assert "invalid arguments" in r["tool_trace"][0]["output"]["error"]


@pytest.mark.db
def test_ungrounded_answer_is_flagged_in_turn(session) -> None:  # type: ignore[no-untyped-def]
    from app.agent.loop import run_turn

    pid, p = _profile(session)
    llm = LLMClient(_Scripted([LLMResult(content="Apply to Google SDE (job_id: 77), pay ₹1,00,000/month.")]), "m",
                    MemoryCache())
    r = run_turn(session, pid, p, "best jobs?", llm=llm)
    assert not r["grounding"]["ok"] and r["grounding"]["unsupported_job_ids"] == [77]


# ---------- fixes after the first real run ----------

def test_digits_in_links_are_not_job_ids() -> None:
    ans = ("TLDP, Johnson & Johnson (job_id: 1303) - [Apply](https://x.greenhouse.io/jobs/7723230003) "
           "https://stripe.com/jobs/search?gh_jid=8130876")
    g = check_answer(ans, OUT)
    assert g.ok and g.job_ids_checked == [1303], g.to_dict()


def test_graduation_year_near_cgpa_not_cgpa_and_profile_values_labeled() -> None:
    g = check_answer("Your MCA (graduating 2027, CGPA 8.41) fits job_id: 1303.", OUT, {2027.0, 8.41})
    kinds = {u["value"]: (u["kind"], u["in_profile"]) for u in g.unsupported_numbers}
    assert kinds["2027"] == ("other", True) and kinds["8.41"] == ("cgpa", True)
    assert not g.ok and g.only_profile_values_flagged


def test_filters_accept_hyphenated_values() -> None:
    from app.agent.tools import SearchFilters

    assert SearchFilters(employment_type="Full-time", work_mode="on-site").model_dump()["employment_type"] == "full_time"
    with pytest.raises(ValueError):
        SearchFilters(employment_type="freelance")


def test_provider_tool_use_failed_is_fed_back() -> None:
    from app.agent.loop import tool_use_feedback

    class E(Exception):
        body = {"error": {"code": "tool_use_failed", "message": "Tool call validation failed: errors: [`/query`: minLength]",
                          "failed_generation": '{"name": "search_jobs", "arguments": {"query": ""}}'}}

    fb = tool_use_feedback(E())
    assert fb and fb["tool"] == "search_jobs" and "minLength" in fb["error"]
    assert tool_use_feedback(Exception("x")) is None
