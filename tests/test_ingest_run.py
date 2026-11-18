"""Ingestion end to end against the test DB with mocked boards: upsert, idempotency, deactivation, reposts."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ingest.http import PoliteClient
from app.ingest.paste import from_paste
from app.ingest.run import run_ingest
from app.ingest.store import upsert_job
from app.models import Job
from app.schemas import CompanyCfg, NormalizedJob

from .conftest import FIXTURES

pytestmark = pytest.mark.db
T0 = datetime(2026, 11, 1, tzinfo=UTC)
GH = "https://boards-api.greenhouse.io/v1/boards/"


def gh_payload() -> dict:
    return json.loads((FIXTURES / "greenhouse_board.json").read_text(encoding="utf-8"))["payload"]


def run(session: Session, payload: dict | None, now: datetime, status: int = 200) -> dict:
    with respx.mock:
        respx.get(url__startswith=GH + "acme").mock(return_value=httpx.Response(status, json=payload or {}))
        return run_ingest(session, [CompanyCfg(name="Acme", source="greenhouse", token="acme")],
                          PoliteClient("t", rps=0, retries=0), process=False, now=now)


def test_ingest_is_idempotent_and_tracks_activity(session: Session) -> None:
    p = gh_payload()
    s1 = run(session, p, T0)
    assert s1["new"] == 3 and s1["filtered_out"] == 1 and s1["to_process"] == 3
    s2 = run(session, p, T0 + timedelta(days=1))
    assert s2["new"] == 0 and s2["unchanged"] == 3 and s2["to_process"] == 0
    assert session.execute(select(func.count(Job.id))).scalar() == 3
    j = session.execute(select(Job).order_by(Job.id)).scalars().first()
    assert j and j.first_seen_at == T0 and j.last_seen_at == T0 + timedelta(days=1)

    # one job disappears -> deactivated; one changes text -> re-processed
    p3 = dict(p, jobs=[dict(p["jobs"][0], content=p["jobs"][0]["content"] + "&lt;p&gt;New line&lt;/p&gt;"), p["jobs"][1]])
    s3 = run(session, p3, T0 + timedelta(days=2))
    assert s3["changed"] == 1 and s3["unchanged"] == 1 and s3["deactivated"] == 1 and s3["to_process"] == 1
    assert session.execute(select(func.count(Job.id)).where(Job.is_active)).scalar() == 2


def test_failed_token_does_not_deactivate(session: Session) -> None:
    run(session, gh_payload(), T0)
    s = run(session, None, T0 + timedelta(days=1), status=404)
    assert s["companies_failed"] == [{"name": "Acme", "status": 404}]
    assert session.execute(select(func.count(Job.id)).where(Job.is_active)).scalar() == 3


def _nj(i: str, title: str = "Backend Engineer") -> NormalizedJob:
    return NormalizedJob(source="lever", source_job_id=i, company="Acme", title=title, description_text=f"text {i}",
                         fetched_at=T0)


def test_repost_detection(session: Session) -> None:
    a, _ = upsert_job(session, _nj("1"), T0)
    b, _ = upsert_job(session, _nj("2", "backend  engineer"), T0 + timedelta(days=10))
    c, _ = upsert_job(session, _nj("3"), T0 + timedelta(days=20))
    d, _ = upsert_job(session, _nj("4"), T0 + timedelta(days=200))  # outside 90-day window of all
    other, _ = upsert_job(session, _nj("5", "Data Analyst"), T0 + timedelta(days=20))
    session.commit()
    assert [a.repost_count, b.repost_count, c.repost_count, d.repost_count, other.repost_count] == [0, 1, 2, 0, 0]


def test_paste_upsert_same_url_updates(session: Session) -> None:
    r1, k1 = upsert_job(session, from_paste("Role: SDE Intern\nstipend 20k", "https://x.test/1"), T0)
    r2, k2 = upsert_job(session, from_paste("Role: SDE Intern\nstipend 25k", "https://x.test/1"), T0)
    assert (k1, k2) == ("new", "changed") and r1.id == r2.id
