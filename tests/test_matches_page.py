"""Matches page: INELIGIBLE jobs after ELIGIBLE and CHECK ones (Matches only), totals, and the name search."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.schemas import EligibilityResult
from app.search.service import MatchItem, _matches_order

PROFILE = {"degree": "MCA", "specialization": "AI", "graduation_year": 2027, "cgpa": 7.9, "experience_years": 0,
           "skills": [{"name": "python"}, {"name": "sql"}], "target_roles": ["data analyst"],
           "preferred_locations": ["Bengaluru"]}
FRESHER = ("Role: Data Analyst Intern\nCompany: {c}\nLocation: Bengaluru\nInternship. Stipend: Rs 15,000 per month.\n"
           "Skills: Python, SQL.\nEligibility: MCA graduates of 2027 batch. Freshers can apply.")
SENIOR = ("Role: Data Analyst\nCompany: {c}\nLocation: Bengaluru\nFull-time.\n"
          "Requirements: 2 - 5 years of experience as a data analyst.\nSkills: Python, SQL, Tableau.")


def _mi(jid: int, score: int, verdict: str) -> MatchItem:
    return MatchItem(job_id=jid, title="t", company="c", url=None, location_raw=None, final_score=score,
                     retrieval_score=0.5, skill_overlap=None, eligibility=EligibilityResult(verdict=verdict, rules=[]))  # type: ignore[arg-type]


def test_matches_order_puts_ineligible_last_whatever_the_score() -> None:
    items = [_mi(1, 90, "INELIGIBLE"), _mi(2, 40, "CHECK"), _mi(3, 70, "ELIGIBLE"), _mi(4, 95, "INELIGIBLE"),
             _mi(5, 70, "CHECK")]
    assert [x.job_id for x in sorted(items, key=_matches_order)] == [3, 5, 2, 4, 1]


def test_final_score_has_no_ineligible_penalty() -> None:
    """The Matches order is separate: final_score (search, agent, evals) still treats INELIGIBLE like ELIGIBLE."""
    from app.config import get_settings
    from app.search.rank import final_score

    cfg = get_settings().cfg.ranking
    assert final_score(0.8, 0.5, "INELIGIBLE", None, cfg) == final_score(0.8, 0.5, "ELIGIBLE", None, cfg)


@pytest.fixture
def setup(sf):  # type: ignore[no-untyped-def]
    from sqlalchemy import text

    from app.db import get_engine
    from app.main import create_app
    from app.models import ProfileRow
    from app.schemas import Profile

    get_engine.cache_clear()
    c = TestClient(create_app())
    ids: dict[str, int] = {}
    for i, (name, tmpl) in enumerate([("Alpha Analytics", FRESHER), ("Beta Analytics", SENIOR),
                                      ("Gamma Analytics", SENIOR), ("Delta 50% Data_Co", FRESHER)]):
        r = c.post("/jobs/paste", json={"text": tmpl.format(c=name), "url": f"https://jobs.example.test/{i}",
                                        "company": name})
        assert r.status_code == 200, r.text
        ids[name] = r.json()["id"]
    with sf() as s:
        s.execute(text("UPDATE jobs SET source = 'lever'"))
        s.execute(text("UPDATE jobs SET is_fresher_relevant = true"))  # keep the senior jobs in scope for this test
        p = Profile.model_validate(PROFILE)
        row = ProfileRow(data=p.model_dump(mode="json"))
        s.add(row)
        s.commit()
        assert s.execute(text("SELECT count(*) FROM jobs WHERE in_scope")).scalar() == 4
        pid = row.id
    return c, pid, ids


def _verdicts(results: list[dict[str, Any]]) -> list[str]:
    return [r["eligibility"]["verdict"] for r in results]


@pytest.mark.db
def test_matches_with_ineligible_lists_them_last_with_totals(setup) -> None:  # type: ignore[no-untyped-def]
    c, pid, _ = setup
    body = c.get(f"/matches/{pid}", params={"show_ineligible": True, "limit": 50}).json()
    v = _verdicts(body["results"])
    assert "INELIGIBLE" in v and v[0] != "INELIGIBLE"
    first_inel = v.index("INELIGIBLE")
    assert all(x == "INELIGIBLE" for x in v[first_inel:]), v
    assert body["total"] == len(body["results"]) == 4 and body["candidates"] == 50
    small = c.get(f"/matches/{pid}", params={"show_ineligible": True, "limit": 1}).json()
    assert len(small["results"]) == 1 and small["total"] == 4
    hidden = c.get(f"/matches/{pid}").json()
    assert "INELIGIBLE" not in _verdicts(hidden["results"]) and hidden["total"] == len(hidden["results"])


@pytest.mark.db
def test_search_order_is_unchanged_by_the_matches_order(setup, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    """search() (used by /search and the agent) still sorts by final score only."""
    import app.search.service as svc

    c, pid, _ = setup
    monkeypatch.setattr(svc, "_matches_order", lambda x: pytest.fail("search must not use the Matches order"))
    r = c.get("/search", params={"q": "data analyst", "profile_id": str(pid), "limit": 10}).json()["results"]
    scores = [(x["final_score"], x["retrieval_score"]) for x in r]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.db
def test_find_by_name_ignores_the_candidate_cap_and_lists_every_verdict(setup, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    from app.config import get_settings

    c, pid, ids = setup
    get_settings().cfg.search.candidates = 1  # Matches now sees one candidate; the name search must not care
    assert len(c.get(f"/matches/{pid}", params={"show_ineligible": True}).json()["results"]) <= 1
    body = c.get(f"/matches/{pid}/find", params={"q": "analytics"}).json()
    got = {r["company"]: r for r in body["results"]}
    assert set(got) == {"Alpha Analytics", "Beta Analytics", "Gamma Analytics"} and body["total"] == 3
    assert got["Beta Analytics"]["eligibility"]["verdict"] == "INELIGIBLE"
    v = _verdicts(body["results"])
    assert v == sorted(v, key=["ELIGIBLE", "CHECK", "INELIGIBLE"].index)
    one = c.get(f"/matches/{pid}/find", params={"q": "gamma"}).json()["results"]
    assert [r["job_id"] for r in one] == [ids["Gamma Analytics"]]


@pytest.mark.db
def test_find_by_name_treats_like_wildcards_literally_and_skips_jobs_without_facts(setup, sf) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import text

    c, pid, ids = setup
    assert [r["job_id"] for r in c.get(f"/matches/{pid}/find", params={"q": "50%"}).json()["results"]] == \
        [ids["Delta 50% Data_Co"]]
    # "_" is literal: "a_a" would match "a A" in "Data Analyst" as a wildcard; literally nothing contains it
    assert c.get(f"/matches/{pid}/find", params={"q": "a_a"}).json()["total"] == 0
    assert c.get(f"/matches/{pid}/find", params={"q": "a_c"}).json()["total"] == 1  # "Data_Co"
    assert c.get(f"/matches/{pid}/find", params={"q": "%%"}).json()["total"] == 0
    assert c.get(f"/matches/{pid}/find", params={"q": "x"}).status_code == 422  # at least 2 characters
    with sf() as s:
        s.execute(text("DELETE FROM job_facts WHERE job_id = :j"), {"j": ids["Gamma Analytics"]})
        s.commit()
    assert c.get(f"/matches/{pid}/find", params={"q": "gamma"}).json()["total"] == 0
