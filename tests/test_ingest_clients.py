"""ATS clients against recorded real API responses (tests/fixtures/*_board.json), served via respx."""

import json
from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.config import get_settings
from app.ingest import ashby, greenhouse, lever
from app.ingest.http import PoliteClient
from app.ingest.normalize import location_matches
from app.ingest.run import select_jobs
from app.schemas import CompanyCfg

from .conftest import FIXTURES

NOW = datetime(2026, 11, 18, tzinfo=UTC)
MODS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby}


def load(source: str) -> dict:
    return json.loads((FIXTURES / f"{source}_board.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("source", ["greenhouse", "lever", "ashby"])
def test_fetch_maps_recorded_board(source: str) -> None:
    fx = load(source)
    mod = MODS[source]
    with respx.mock:
        respx.get(url__startswith=mod.URL.format(token=fx["token"])).mock(
            return_value=httpx.Response(200, json=fx["payload"]))
        res = mod.fetch(PoliteClient("t", rps=0), CompanyCfg(name=fx["company"], source=source, token=fx["token"]), NOW)
    assert res.ok and len(res.jobs) == 4
    for j in res.jobs:
        assert j.source == source and j.company == fx["company"] and j.title and j.source_job_id
        assert j.url and j.url.startswith("https://")
        assert len(j.description_text) > 200 and "<p>" not in j.description_text and "&lt;" not in j.description_text
        assert j.posted_at is not None and j.posted_at.tzinfo is not None
    cfg = get_settings().cfg.ingest
    assert sum(location_matches(j.location_raw, cfg) for j in res.jobs) == 3  # 3 matching + 1 other recorded


@pytest.mark.parametrize("source", ["greenhouse", "lever", "ashby"])
def test_fetch_bad_token_is_not_ok(source: str) -> None:
    mod = MODS[source]
    with respx.mock:
        respx.get(url__startswith=mod.URL.format(token="nope")).mock(return_value=httpx.Response(404, json={}))
        res = mod.fetch(PoliteClient("t", rps=0, retries=0), CompanyCfg(name="X", source=source, token="nope"), NOW)
    assert not res.ok and res.status == 404 and res.jobs == []


def test_select_jobs_cap_prefers_india(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.schemas import NormalizedJob

    s = get_settings()
    s.cfg.ingest.max_jobs_per_company = 2
    jobs = [
        NormalizedJob(source="lever", source_job_id=str(i), company="C", title="T", location_raw=loc,
                      description_text="d", posted_at=datetime(2026, 1, i + 1, tzinfo=UTC), fetched_at=NOW)
        for i, loc in enumerate(["Remote", "Pune, India", "Remote", "Berlin", "Bengaluru"])
    ]
    kept = select_jobs(jobs, s)
    assert [j.location_raw for j in kept] == ["Bengaluru", "Pune, India"]
