"""Fields the UI cards read: stored facts on match items, eligibility on a job, grounding on an explanation."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

PASTE = ("Role: Software Engineer Intern\nCompany: Card Labs\nLocation: Bengaluru\n"
         "Stipend: Rs 20,000 per month. Duration: 6 months internship.\nSkills: Python, SQL, Java.\n"
         "Eligibility: B.Tech graduates of 2026 batch. Freshers can apply.")


@pytest.fixture
def client(sf):  # type: ignore[no-untyped-def]
    from app.db import get_engine
    from app.main import create_app

    get_engine.cache_clear()
    return TestClient(create_app())


@pytest.mark.db
def test_match_items_carry_stored_facts(client: TestClient) -> None:
    jid = client.post("/jobs/paste", json={"text": PASTE}).json()["id"]
    pid = client.get("/profiles/demo").json()["id"]
    items = client.get(f"/matches/{pid}", params={"show_ineligible": True}).json()["results"]
    it = next(i for i in items if i["job_id"] == jid)
    for k in ("work_mode", "employment_type", "pay", "apply_deadline", "expired", "eligibility", "trust"):
        assert k in it, k
    assert it["pay"]["currency"] == "INR" and it["pay"]["inr_per_month"] == 20000 and it["expired"] is False
    assert {r["status"] for r in it["eligibility"]["rules"]} <= {"PASS", "FAIL", "UNKNOWN", "SOFT_FAIL"}


@pytest.mark.db
def test_job_with_profile_has_eligibility_and_explain_has_grounding(client: TestClient) -> None:
    jid = client.post("/jobs/paste", json={"text": PASTE}).json()["id"]
    pid = client.get("/profiles/demo").json()["id"]
    plain = client.get(f"/jobs/{jid}").json()
    assert plain["eligibility"] is None and plain["expired"] is False and plain["facts"]["pay"]["evidence"]
    j = client.get(f"/jobs/{jid}", params={"profile_id": pid}).json()
    assert j["eligibility"]["verdict"] in ("ELIGIBLE", "CHECK", "INELIGIBLE")
    e = client.get(f"/matches/{pid}/{jid}/explain").json()
    assert set(e["grounding"]) >= {"matched_dropped", "missing_dropped", "rule", "ok"}


@pytest.mark.db
def test_evals_summary_has_sample_sizes_and_review_counts(client: TestClient) -> None:
    s = client.get("/evals/summary").json()
    h = s["retrieval_e1"]["groups"]["human_only"]
    assert all(m["profiles"] == 5 and m["labels"] == 49 for m in h.values())
    assert s["trust_classifier"]["split"]["test"] == 1788 and "not validated" in s["trust_classifier"]["data"]["caveat"]
    assert s["label_agreement"]["groups"]["all"]["n"] == 49
    run4 = next(r for r in s["agent_runs"] if r["run"] == "agent_run4")
    assert run4["conversations"] == 15 and run4["manual_review"].startswith("7 correct")


@pytest.mark.db
def test_demo_profile_cannot_store_memory(client: TestClient) -> None:
    from app.agent.tools import RememberArgs, ToolContext, remember
    from app.db import session_factory
    from app.demo import DEMO_PROFILE, DEMO_PROFILE_ID

    client.get("/profiles/demo")
    with session_factory()() as s:
        out = remember(ToolContext(session=s, profile_id=DEMO_PROFILE_ID, profile=DEMO_PROFILE, memory={}),
                       RememberArgs(key="preferred_locations", value=["Pune"]))
    assert "error" in out and "demo profile" in out["error"]
