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
    g = check_answer("Here are 3 options:\n1. Job A (job_id: 1303) CHECK\n2) none else", OUT)
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
    ans = ("TLDP, Johnson & Johnson (job_id: 1303) CHECK - [Apply](https://x.greenhouse.io/jobs/7723230003) "
           "https://stripe.com/jobs/search?gh_jid=8130876")
    g = check_answer(ans, OUT)
    assert g.ok and g.job_ids_checked == [1303], g.to_dict()


def test_profile_and_user_message_values_accepted_only_if_real() -> None:
    ans = "Your MCA (graduating 2027, CGPA 8.41) fits job_id: 1303 (CHECK)."
    g = check_answer(ans, OUT, {2027.0, 8.41})
    assert g.ok and not g.ok_tools_only and g.accepted_from_profile == ["2027", "8.41"]
    g2 = check_answer(ans, OUT, {2026.0, 8.0})  # not the real profile values -> flagged
    assert not g2.ok and {u["value"] for u in g2.unsupported_numbers} == {"2027", "8.41"}
    g3 = check_answer("Jobs paying at least ₹50,000 a month: job_id: 1303 CHECK", OUT, set(), "pay at least 50k")
    assert g3.ok and g3.accepted_from_user_message == ["₹50,000"]


def test_verdict_missing_and_mismatch() -> None:
    g = check_answer("- TLDP, Johnson & Johnson (job_id: 1303)\n- nothing else", OUT)
    assert not g.ok and g.verdict_missing == [1303]
    g = check_answer("- TLDP, Johnson & Johnson (job_id: 1303): ELIGIBLE", OUT)
    assert g.verdict_mismatch == [{"job_id": 1303, "tool": "CHECK", "answer": ["ELIGIBLE"]}]
    g = check_answer("- TLDP (job_id: 1303): CHECK; please check the batch.", OUT)
    assert g.ok and g.verdict_mismatch == [] and g.verdict_missing == []


def test_tool_blocks_explicit_trust_eligibility_and_pay() -> None:
    from types import SimpleNamespace

    from app.agent.tools import _pay, trust_block
    from app.schemas import JobFacts

    t = SimpleNamespace(level="ok", score=90, reasons=[{"code": "age_60", "message": "Posted 70 days ago", "delta": -15},
                                                      {"code": "classifier", "message": "p=0.1", "delta": -4}])
    b = trust_block(t, JobFacts(title="x"))  # type: ignore[arg-type]
    assert b["scam_rules_fired"] == [] and b["ghost_signals"][0]["code"] == "age_60" and b["posting_red_flags"] == []
    assert trust_block(None, None)["scam_rules_fired"] is None
    f = JobFacts.model_validate({"title": "x", "pay": {"value": {"min": 74000, "max": 148000, "currency": "USD",
                                                                 "period": "year"}, "evidence": "USD 74,000"}})
    p = _pay(f)
    assert p and p["currency"] == "USD" and "inr_per_month" not in p and "not converted" in p["note"]


def test_budget_counts_each_purpose_separately() -> None:
    from app.llm.client import BudgetExceeded
    from app.llm.fake import FakeProvider

    cache = MemoryCache()
    cache.put("k1", "m", "facts_v3", {}, 900_000, 0)  # big extraction day
    llm = LLMClient(FakeProvider(), "m", cache, daily_token_budget=1000, purpose_budgets={"agent": 50})
    llm.chat([{"role": "user", "content": "hi"}], prompt_version="explain_v1", essential=False)  # own count only
    assert cache.tokens_today("facts") == 900_000 and cache.tokens_today("agent") == 0
    cache.put("k2", "m", "agent_v2", {}, 60, 0)
    with pytest.raises(BudgetExceeded, match="'agent'"):
        llm.chat([{"role": "user", "content": "x"}], prompt_version="agent_v2", essential=False)


@pytest.mark.db
def test_skipped_explanation_is_disclosed_and_compare_has_needs_checking(client, session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.explain as ex
    from app.agent.loop import run_turn
    from app.agent.tools import CompareJobsArgs, compare_jobs

    a = client.post("/jobs/paste", json={"text": PASTE}).json()
    b = client.post("/jobs/paste", json={"text": PASTE.replace("Example Analytics", "Other Co")}).json()
    pid, p = _profile(session)
    ctx = ToolContext(session=session, profile_id=pid, profile=p, memory={})
    rows = compare_jobs(ctx, CompareJobsArgs(job_ids=[a["id"], b["id"]]))
    assert rows["jobs_compared"] == 2
    assert all(isinstance(r["needs_checking"], list) and "trust" in r for r in rows["jobs"])

    monkeypatch.setattr(ex, "explain_match", lambda *a, **k: {"error": "Daily token budget for 'explain' reached"})
    call = LLMResult(content=None, tool_calls=[ToolCall(id="1", name="explain_match",
                                                        arguments=json.dumps({"job_id": a["id"]}))])
    final = LLMResult(content=f"Data Analyst Intern, Example Analytics (job_id: {a['id']}) fits you.")
    llm = LLMClient(_Scripted([call, final]), "m", MemoryCache())
    r = run_turn(session, pid, p, "explain", llm=llm)
    assert "explanation was skipped" in r["answer"] and r["explanations_skipped"][0]["job_id"] == a["id"]


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


# ---------- run-4 fixes: per-job rule lists, job counts, agent_v3 ----------

SEARCH_OUT = [{"jobs_returned": 3, "results": [
    {"job_id": 350, "title": "Associate SDE Intern", "company": "Hevo Data", "eligibility": "INELIGIBLE",
     "needs_checking": ["graduation_year", "cgpa"], "failed": ["degree"], "ghost_signals": []},
    {"job_id": 1149, "title": "Frontend Engineer", "company": "Razorpay", "eligibility": "INELIGIBLE",
     "needs_checking": ["degree", "graduation_year"], "failed": ["experience"], "ghost_signals": ["age_60"]},
    {"job_id": 1091, "title": "AI Platform Engineer", "company": "Supabase", "eligibility": "CHECK",
     "needs_checking": ["degree", "employment_type"], "failed": [], "ghost_signals": []}]}]


def test_copied_rule_list_is_flagged() -> None:
    ans = ("1. Associate SDE Intern, Hevo Data (job_id: 350) – INELIGIBLE\n   Failed: degree\n"
           "2. Frontend Engineer, Razorpay (job_id: 1149) – INELIGIBLE\n   Failed: degree\n"
           "3. AI Platform Engineer, Supabase (job_id: 1091) – CHECK\n   Needs checking: degree, cgpa\n")
    g = check_answer(ans, SEARCH_OUT)
    bad = {x["job_id"]: x["rules"] for x in g.rules_not_in_job_output}
    assert bad == {1091: ["cgpa"]}  # 1149 lists degree under needs_checking, so "degree" is in its lists
    assert not g.ok


def test_correct_per_job_rules_pass() -> None:
    ans = ("- Associate SDE Intern, Hevo Data (job_id: 350) – INELIGIBLE – failed: degree\n"
           "- Frontend Engineer, Razorpay (job_id: 1149) – INELIGIBLE – failed: experience; ghost signal age_60\n"
           "- AI Platform Engineer, Supabase (job_id: 1091) – CHECK – needs checking: degree, employment type\n"
           "Jobs checked: 3.")
    g = check_answer(ans, SEARCH_OUT)
    assert g.ok, g.to_dict()


def test_rules_for_unchecked_job_flagged() -> None:
    out = [{"jobs_returned": 1, "results": [{"job_id": 7, "title": "SDE", "company": "X", "eligibility": "CHECK"}]}]
    g = check_answer("SDE, X (job_id: 7) – CHECK – needs checking: degree, cgpa", out)
    assert g.rules_for_unchecked_job == [{"job_id": 7, "rules": ["cgpa", "degree"]}] and not g.ok


def test_wrong_job_count_flagged() -> None:
    g = check_answer("Jobs checked: 7 (6 results + 1 name-match)\n- Associate SDE Intern, Hevo Data (job_id: 350) – "
                     "INELIGIBLE", SEARCH_OUT)
    assert [x["stated"] for x in g.wrong_job_counts] == [7] and not g.ok
    assert check_answer("I checked 3 jobs.", SEARCH_OUT).wrong_job_counts == []


def test_search_results_carry_rule_lists(client, session) -> None:  # type: ignore[no-untyped-def]
    from app.agent.tools import search_jobs

    client.post("/jobs/paste", json={"text": PASTE})
    pid, p = _profile(session)
    out = search_jobs(ToolContext(session=session, profile_id=pid, profile=p, memory={}),
                      SearchJobsArgs(query="zzz Example"))
    rows = out["results"] + (out["name_matches"] or [])
    assert rows and all(isinstance(r["needs_checking"], list) and isinstance(r["failed"], list) for r in rows)


test_search_results_carry_rule_lists = pytest.mark.db(test_search_results_carry_rule_lists)


def test_agent_v3_prompt_rules() -> None:
    from app.extract.prompting import load_prompt

    text = load_prompt("agent_v3")[0]
    for must in ("Never copy one job's rules onto another job", "rules were not checked",
                 'Never say "no red flags"', "say how many there are", "were not checked"):
        assert must in text, must


# ---------- run-5 fixes: job facts in search results, "not listed" claims, agent_v4 ----------

def test_agent_v4_prompt_rules_and_config() -> None:
    from app.config import get_settings
    from app.extract.prompting import load_prompt

    assert get_settings().cfg.prompts.agent == "agent_v4"
    text = load_prompt("agent_v4")[0]
    for must in ("pay, apply_deadline, work_mode and employment_type", 'only when that field is null',
                 "Never copy one job's rules onto another job", "never generalize over the listed jobs",
                 "Use a tool for every question", '"expired": true'):
        assert must in text, must


FACTS_OUT = [{"jobs_returned": 2, "results": [
    {"job_id": 1, "title": "SDE Intern", "company": "A", "eligibility": "CHECK", "pay": None, "apply_deadline": None},
    {"job_id": 2, "title": "TLDP", "company": "B", "eligibility": "CHECK",
     "pay": {"min": 1565000, "max": 1565000, "currency": "INR", "period": "year"}, "apply_deadline": "2026-12-01"}]}]


def test_not_listed_claim_ok_only_when_field_null() -> None:
    g = check_answer("SDE Intern, A (job_id: 1) – CHECK – pay not listed, no deadline stated", FACTS_OUT)
    assert g.ok and g.unsupported_not_stated == [], g.to_dict()
    g = check_answer("TLDP, B (job_id: 2) – CHECK. No pay or deadline listed.", FACTS_OUT)
    assert {(x["job_id"], x["field"]) for x in g.unsupported_not_stated} == {(2, "pay"), (2, "apply_deadline")}
    assert not g.ok and "rules_not_in_job_output" not in {k for k, v in g.to_dict().items() if v}


def test_not_listed_claim_for_unfetched_job_and_answer_wide_line() -> None:
    out = [{"jobs_returned": 1, "results": [{"job_id": 7, "title": "SDE", "company": "X", "eligibility": "CHECK"}]}]
    g = check_answer("SDE, X (job_id: 7) – CHECK\nNone of the postings listed a salary.", out)
    assert g.unsupported_not_stated[0]["job_id"] == 7 and "not in this turn" in g.unsupported_not_stated[0]["why"]
    g = check_answer("SDE Intern, A (job_id: 1) – CHECK\nTLDP, B (job_id: 2) – CHECK\nNone of these list a salary.",
                     FACTS_OUT)
    assert [x["job_id"] for x in g.unsupported_not_stated] == [2]


def test_rule_scan_stops_at_clause_end() -> None:
    from app.agent.validate import _rules_named

    assert _rules_named("CHECK [needs checking: graduation_year, location] — no scam signals. No pay listed.") == {
        "graduation_year", "location"}


@pytest.mark.db
def test_search_results_carry_job_facts(client, session) -> None:  # type: ignore[no-untyped-def]
    from app.agent.tools import search_jobs

    client.post("/jobs/paste", json={"text": PASTE})
    pid, p = _profile(session)
    out = search_jobs(ToolContext(session=session, profile_id=pid, profile=p, memory={}),
                      SearchJobsArgs(query="zzz Example"))
    rows = out["results"] + (out["name_matches"] or [])
    assert rows
    for r in rows:
        assert {"pay", "apply_deadline", "work_mode", "employment_type"} <= r.keys()
    r = rows[0]
    assert r["pay"]["currency"] == "INR" and r["pay"]["inr_per_month"] == 15000 and r["employment_type"] == "internship"
