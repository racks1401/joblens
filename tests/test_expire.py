"""Expired jobs: deadline before today (IST) -> expired; kept, hidden from default matches/search/agent search."""

from __future__ import annotations

from datetime import date

import pytest

from app.ingest.expire import is_expired

PASTE = ("Role: Data Analyst Intern\nCompany: Expiry Analytics\nLocation: Bengaluru\n"
         "Stipend: Rs 15,000 per month. Duration: 6 months internship.\nSkills: Python, SQL, Excel.\n"
         "Eligibility: MCA graduates of 2027 batch. Freshers can apply.")
PROFILE = {"degree": "MCA", "specialization": "AI", "graduation_year": 2027, "cgpa": 7.9,
           "skills": [{"name": "python"}, {"name": "sql"}], "target_roles": ["data analyst"]}
TODAY = date(2026, 11, 20)


def test_is_expired_boundary() -> None:
    assert is_expired(date(2026, 11, 19), TODAY)
    assert not is_expired(TODAY, TODAY)  # deadline today: still open
    assert not is_expired(date(2026, 12, 1), TODAY)
    assert not is_expired(None, TODAY)


@pytest.fixture
def client(sf):  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from app.db import get_engine
    from app.main import create_app

    get_engine.cache_clear()
    with TestClient(create_app()) as c:
        yield c


def _set_deadline(session, job_id: int, d: date | None) -> None:  # type: ignore[no-untyped-def]
    from app.models import JobFactsRow

    fr = session.get(JobFactsRow, job_id)
    facts = dict(fr.facts)
    facts["apply_deadline"] = {"value": d.isoformat() if d else None, "evidence": "Apply by" if d else None}
    fr.facts = facts
    session.commit()


@pytest.mark.db
def test_expired_job_kept_hidden_from_search_and_matches(client, session) -> None:  # type: ignore[no-untyped-def]
    from app.agent.tools import JobIdArgs, SearchJobsArgs, ToolContext, get_job, search_jobs
    from app.ingest.expire import apply, plan
    from app.models import Job, ProfileRow
    from app.schemas import Profile
    from app.search.service import match, search

    jid = client.post("/jobs/paste", json={"text": PASTE}).json()["id"]
    keep = client.post("/jobs/paste", json={"text": PASTE.replace("Expiry Analytics", "Open Co")}).json()["id"]
    _set_deadline(session, jid, date(2026, 11, 19))
    _set_deadline(session, keep, TODAY)

    ch = plan(session, TODAY)
    assert [c["job_id"] for c in ch] == [jid] and ch[0]["expired_new"] is True
    assert apply(session, ch) == 1
    session.expire_all()
    assert session.get(Job, jid).expired and not session.get(Job, keep).expired
    assert plan(session, TODAY) == []  # idempotent

    p = Profile.model_validate(PROFILE)
    row = ProfileRow(data=p.model_dump())
    session.add(row)
    session.commit()
    ctx = ToolContext(session=session, profile_id=row.id, profile=p, memory={})

    out = search_jobs(ctx, SearchJobsArgs(query="Expiry Analytics data analyst"))
    ids = {r["job_id"] for r in out["results"] + (out["name_matches"] or [])}
    assert jid not in ids and keep in ids

    assert jid not in {r.job_id for r in search(session, "data analyst intern", profile=p)["results"]}
    assert jid in {r.job_id for r in search(session, "data analyst intern", profile=p, include_expired=True)["results"]}
    assert jid not in {m.job_id for m in match(session, p, show_ineligible=True)}
    assert jid in {m.job_id for m in match(session, p, show_ineligible=True, include_expired=True)}
    assert client.get(f"/matches/{row.id}", params={"show_ineligible": True}).status_code == 200
    got = {r["job_id"] for r in client.get(f"/matches/{row.id}", params={"show_ineligible": True}).json()["results"]}
    assert jid not in got and keep in got

    card = get_job(ctx, JobIdArgs(job_id=jid))
    assert card["expired"] is True and card["apply_deadline"] == date(2026, 11, 19)

    # a later deadline (e.g. corrected extraction) clears the flag
    _set_deadline(session, jid, date(2026, 12, 31))
    ch = plan(session, TODAY)
    assert [(c["job_id"], c["expired_new"]) for c in ch] == [(jid, False)]
