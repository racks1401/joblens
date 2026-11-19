"""Multi-role splitter: the LLM gives line ranges only; pieces are original lines; unheaded roles are flagged."""

import json
from typing import Any

import pytest

from app.ingest.split import NEEDS_HUMAN_TITLE, build_pieces, numbered, plan_from_answer, split_post
from app.llm.cache import MemoryCache
from app.llm.client import LLMClient, LLMResult
from app.llm.fake import FakeProvider

POST = "\n".join([
    "Company: Acme",                       # 1  header
    "CGPA Required - 7.5 and above",       # 2  header
    "",                                    # 3
    "Backend Intern",                      # 4  role 1 heading
    "1. Stipend: INR 50,000 per month",    # 5
    "",                                    # 6
    "Data Analyst",                        # 7  role 2 heading
    "1. CTC: 12 LPA",                      # 8
    "",                                    # 9
    "1. Stipend: INR 40,000 per month",    # 10 role 3 (no heading)
    "2. Eligibility: Post Graduate only",  # 11
    "",                                    # 12
    "Deadline - 30/11/2026",               # 13 footer
])
PLAN = {"header": [1, 2], "footer": [13, 13],
        "roles": [{"start": 4, "end": 5, "heading": 4}, {"start": 7, "end": 8, "heading": 7},
                  {"start": 10, "end": 11, "heading": None}]}


def llm_returning(answer: Any) -> LLMClient:
    content = answer if isinstance(answer, str) else json.dumps(answer)
    return LLMClient(FakeProvider({"split": lambda m, t: LLMResult(content=content)}), "fake", MemoryCache())


def test_split_builds_original_pieces_with_shared_header_and_footer() -> None:
    pieces = split_post(POST, llm_returning(PLAN))
    assert [p.title for p in pieces] == ["Backend Intern", "Data Analyst", NEEDS_HUMAN_TITLE]
    lines = POST.split("\n")
    for p in pieces:
        assert p.text.startswith("Company: Acme\nCGPA Required - 7.5 and above")  # header copied
        assert p.text.endswith("Deadline - 30/11/2026")  # footer copied
        assert all(ln in lines for ln in p.text.split("\n"))  # never rewritten
    assert "Post Graduate only" in pieces[2].text and "Post Graduate only" not in pieces[0].text


@pytest.mark.parametrize("bad", [
    {"roles": [{"start": 4, "end": 8, "heading": 4}, {"start": 7, "end": 11, "heading": 7}]},  # overlap
    {"roles": [{"start": 4, "end": 99, "heading": 4}, {"start": 7, "end": 8, "heading": 7}]},  # out of range
    {"header": [1, 5], "roles": [{"start": 4, "end": 5, "heading": 4}, {"start": 7, "end": 8, "heading": 7}]},
    {"roles": [{"start": 4, "end": 5, "heading": 9}, {"start": 7, "end": 8, "heading": 7}]},  # heading outside
    "not json",
])
def test_invalid_plans_mean_no_split(bad: Any) -> None:
    assert split_post(POST, llm_returning(bad)) == []


def test_single_role_is_not_split() -> None:
    assert split_post(POST, llm_returning({"roles": [{"start": 1, "end": 13, "heading": 4}]})) == []


def test_numbered_and_plan_helpers() -> None:
    assert numbered("a\nb").split("\n") == ["1| a", "2| b"]
    header, footer, roles = plan_from_answer(PLAN, 13)
    pieces = build_pieces(POST, header, footer, roles)
    assert pieces[1].lines == (7, 8)


@pytest.mark.db
def test_paste_route_creates_one_job_per_role(sf, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    import app.llm.client as client_mod
    from app.db import get_engine
    from app.main import create_app
    from app.models import Job

    real_get_llm = client_mod.get_llm

    def fake_get_llm(role: str = "extract", cache: Any = None, model: Any = None) -> LLMClient:
        llm = real_get_llm(role, cache, model)
        llm.provider = FakeProvider({"split": lambda m, t: LLMResult(content=json.dumps(PLAN))})
        return llm

    monkeypatch.setattr(client_mod, "get_llm", fake_get_llm)
    get_engine.cache_clear()
    r = TestClient(create_app()).post("/jobs/paste", json={"text": POST})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["related_job_ids"]) == 2
    with sf() as s:
        jobs = s.query(Job).order_by(Job.id).all()
        assert [j.title for j in jobs] == ["Backend Intern", "Data Analyst", NEEDS_HUMAN_TITLE]
        assert all(j.company == "Acme" and j.in_scope for j in jobs)
    flagged = TestClient(create_app()).get(f"/jobs/{body['related_job_ids'][1]}").json()
    assert flagged["flags"] == ["needs human title"]
