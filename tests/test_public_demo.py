"""Public-demo safety: UI-to-API key, visitor-IP rate limit, paste turned off, MiniLM only, and the seed script."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
KEY = "ui-key-for-tests-0123456789"
PASTE = ("Role: Data Analyst Intern\nCompany: Public Analytics\nLocation: Bengaluru\n"
         "Stipend: Rs 15,000 per month.\nSkills: Python, SQL.\nEligibility: MCA graduates of 2027 batch.")


def _public(on: bool = True) -> None:
    from app.config import get_settings

    get_settings().env.public_demo = on


def _public_with_key(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    """Public-demo mode as deployed: the API has UI_API_KEY and the client (the UI) sends it."""
    _public()
    monkeypatch.setenv("UI_API_KEY", KEY)
    client.headers["X-UI-Key"] = KEY


@pytest.fixture
def client(sf):  # type: ignore[no-untyped-def]
    from app.db import get_engine
    from app.main import create_app

    get_engine.cache_clear()
    return TestClient(create_app())


@pytest.fixture
def app_client():  # type: ignore[no-untyped-def]
    """App without a database, for checks that return before any route runs."""
    from app.main import create_app

    return TestClient(create_app())


# --- UI-to-API key


def test_ui_key_unset_leaves_local_runs_open(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.deps import ui_key_error

    monkeypatch.delenv("UI_API_KEY", raising=False)
    assert ui_key_error("/jobs/1", None) is None


def test_ui_key_required_on_every_route_but_health(monkeypatch: pytest.MonkeyPatch, app_client: TestClient) -> None:
    monkeypatch.setenv("UI_API_KEY", KEY)
    for path in ("/limits", "/jobs/1", "/profiles/demo", "/evals/summary", "/docs", "/openapi.json"):
        r = app_client.get(path)
        assert r.status_code == 401 and r.json()["error"]["code"] == "ui_key_required", path
        assert app_client.get(path, headers={"X-UI-Key": KEY + "x"}).status_code == 401, path
    assert app_client.post("/chat", json={}).status_code == 401
    assert app_client.post("/admin/ingest", headers={"X-API-Key": "test-admin-key"}).status_code == 401
    assert app_client.get("/health").status_code == 200


@pytest.mark.db
def test_ui_key_lets_the_ui_through(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    monkeypatch.setenv("UI_API_KEY", KEY)
    r = client.get("/limits", headers={"X-UI-Key": KEY})
    assert r.status_code == 200 and r.json()["public_demo"] is False
    assert client.get("/profiles/demo", headers={"X-UI-Key": KEY}).status_code == 200


def test_public_demo_without_a_key_is_closed(monkeypatch: pytest.MonkeyPatch, app_client: TestClient) -> None:
    monkeypatch.delenv("UI_API_KEY", raising=False)
    _public()
    r = app_client.get("/limits")
    assert r.status_code == 503 and r.json()["error"]["code"] == "ui_key_not_configured"
    h = app_client.get("/health")
    assert h.status_code == 200 and h.json()["public_demo"] is True


@pytest.mark.parametrize("weak", ["change-me", "short-key"])
def test_weak_ui_key_closes_the_api(monkeypatch: pytest.MonkeyPatch, app_client: TestClient, weak: str) -> None:
    monkeypatch.setenv("UI_API_KEY", weak)
    r = app_client.get("/limits", headers={"X-UI-Key": weak})
    assert r.status_code == 503 and r.json()["error"]["code"] == "ui_key_misconfigured"


def test_ui_sends_the_key_and_visitor_header(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    sys.path.insert(0, str(ROOT / "ui"))
    monkeypatch.setenv("UI_API_KEY", KEY)
    import lib

    lib = importlib.reload(lib)
    monkeypatch.setattr(lib, "client_session_id", lambda: "sess-1")
    monkeypatch.setattr(lib, "visitor_forwarded_for", lambda: "203.0.113.7")
    assert lib.api_headers() == {"X-Session-Id": "sess-1", "X-UI-Key": KEY, "X-Visitor-Forwarded-For": "203.0.113.7"}


# --- visitor IP limit


@pytest.mark.parametrize(("xff", "hops", "ip"), [
    ("203.0.113.7", 1, "203.0.113.7"),
    ("6.6.6.6, 203.0.113.7", 1, "203.0.113.7"),      # a forged left entry is ignored
    ("6.6.6.6, 203.0.113.7, 10.0.0.2", 2, "203.0.113.7"),
    ("203.0.113.7", 2, None),                         # fewer entries than trusted proxies
    ("not-an-ip", 1, None),
    (None, 1, None),
    ("2001:db8::1", 1, "2001:db8::1"),
])
def test_visitor_ip_takes_the_trusted_entry(xff: str | None, hops: int, ip: str | None) -> None:
    from app.api.limits import visitor_ip

    assert visitor_ip(xff, hops) == ip


def test_ip_key_is_a_hash_and_unknown_ips_share_a_bucket_only_in_public_mode() -> None:
    from app.api.limits import UNKNOWN_IP, ip_key

    k = ip_key("203.0.113.7", public_demo=False)
    assert k is not None and k.startswith("ip:") and "203.0.113.7" not in k
    assert ip_key(None, public_demo=False) is None
    assert ip_key(None, public_demo=True) == UNKNOWN_IP


def _limits(per_session: int, per_ip: int, per_day: int) -> None:
    from app.config import ActionLimit, get_settings

    get_settings().env.demo_limits = True
    cfg = get_settings().cfg.limits
    cfg.enabled, cfg.proxy_hops = True, 1
    for a in ("chat", "paste", "explain", "resume"):
        cfg.actions[a] = ActionLimit(per_session=per_session, per_ip=per_ip, per_day=per_day)


@pytest.mark.db
def test_ip_limit_holds_across_new_sessions(client: TestClient) -> None:
    _limits(per_session=10, per_ip=2, per_day=100)
    pid = client.get("/profiles/demo").json()["id"]

    def chat(session: str, xff: str) -> Any:
        return client.post("/chat", json={"profile_id": pid, "message": "data jobs"},
                           headers={"X-Session-Id": session, "X-Visitor-Forwarded-For": xff})

    assert chat("reload-1", "203.0.113.7").status_code == 200
    assert chat("reload-2", "6.6.6.6, 203.0.113.7").status_code == 200  # forged left entry: same visitor
    r = chat("reload-3", "7.7.7.7, 203.0.113.7")
    assert r.status_code == 429 and r.json()["error"]["code"] == "ip_limit_reached"
    assert chat("other-visitor", "198.51.100.9").status_code == 200
    lim = client.get("/limits", headers={"X-Session-Id": "reload-3",
                                         "X-Visitor-Forwarded-For": "203.0.113.7"}).json()["actions"]["chat"]
    assert lim["ip_used"] == 2 and lim["per_ip"] == 2 and lim["day_used"] == 3


@pytest.mark.db
def test_ip_limit_is_skipped_locally_without_a_visitor_ip(client: TestClient) -> None:
    _limits(per_session=10, per_ip=1, per_day=100)
    pid = client.get("/profiles/demo").json()["id"]
    for i in range(3):
        r = client.post("/chat", json={"profile_id": pid, "message": "hi"}, headers={"X-Session-Id": f"s{i}"})
        assert r.status_code == 200


@pytest.mark.db
def test_public_mode_puts_requests_without_ip_in_one_bucket(client: TestClient,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    _limits(per_session=10, per_ip=1, per_day=100)
    _public_with_key(monkeypatch, client)
    pid = client.get("/profiles/demo").json()["id"]
    assert client.post("/chat", json={"profile_id": pid, "message": "hi"}, headers={"X-Session-Id": "a"}).status_code == 200
    r = client.post("/chat", json={"profile_id": pid, "message": "hi"}, headers={"X-Session-Id": "b"})
    assert r.status_code == 429 and r.json()["error"]["code"] == "ip_limit_reached"


# --- pasted jobs


@pytest.mark.db
def test_public_mode_refuses_paste_and_stores_nothing(client: TestClient, session, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import text

    _limits(per_session=5, per_ip=5, per_day=5)
    _public_with_key(monkeypatch, client)
    r = client.post("/jobs/paste", json={"text": PASTE}, headers={"X-Session-Id": "s1"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "paste_disabled"
    assert session.execute(text("SELECT count(*) FROM jobs")).scalar() == 0
    assert session.execute(text("SELECT count(*) FROM usage_counts")).scalar() == 0  # refused before counting
    assert client.get("/limits").json()["public_demo"] is True


@pytest.mark.db
def test_paste_works_outside_public_mode(client: TestClient) -> None:
    r = client.post("/jobs/paste", json={"text": PASTE})
    assert r.status_code == 200 and r.json()["source"] == "paste"


# --- embedding models


def test_public_mode_embeds_and_serves_the_default_model_only() -> None:
    from app.search.embed import model_keys, serving_model

    assert set(model_keys()) == {"minilm", "bge-small"}
    assert serving_model("bge-small") == "bge-small"
    _public()
    assert model_keys() == ["minilm"]
    assert serving_model("bge-small") == "minilm" and serving_model(None) == "minilm"


@pytest.mark.db
def test_public_mode_ignores_the_search_model_parameter(client: TestClient, session, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import text

    import app.search.service as svc
    from app.search.embed import get_embedder

    client.post("/jobs/paste", json={"text": PASTE})
    _public_with_key(monkeypatch, client)
    used: list[str] = []
    monkeypatch.setattr(svc, "get_embedder", lambda key=None: used.append(key) or get_embedder(key))
    for model in ("bge-small", "nope"):
        r = client.get("/search", params={"q": "python", "mode": "vector", "model": model})
        assert r.status_code == 200, r.text
    assert used and set(used) == {"minilm"}
    # and a job processed in public mode gets MiniLM embeddings only
    from app.search.index import process_jobs

    jid = session.execute(text("SELECT id FROM jobs LIMIT 1")).scalar()
    session.execute(text("DELETE FROM job_embeddings"))
    session.commit()
    process_jobs(session, [jid], models=["minilm", "bge-small"])
    models = session.execute(text("SELECT DISTINCT model_name FROM job_embeddings")).scalars().all()
    assert models == ["minilm"]


# --- seed script


def _seed_module() -> Any:
    spec = importlib.util.spec_from_file_location("seed_prod", ROOT / "scripts" / "seed_prod.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_seed_column_lists_match_the_models() -> None:
    from app.models import Job, JobEmbedding, JobFactsRow, TrustScore

    seed = _seed_module()
    for model, cols in ((Job, seed.JOB_COLUMNS), (JobFactsRow, seed.FACT_COLUMNS), (TrustScore, seed.TRUST_COLUMNS),
                        (JobEmbedding, seed.EMBEDDING_COLUMNS)):
        assert cols == [c.name for c in model.__table__.columns if c.computed is None], model.__tablename__


@pytest.mark.db
def test_seed_copies_only_public_jobs_and_minilm(client: TestClient, sf, db_url: str) -> None:  # type: ignore[no-untyped-def]
    import psycopg
    from sqlalchemy import text

    for i in range(2):
        assert client.post("/jobs/paste", json={"text": PASTE.replace("Public", f"Board{i}"),
                                                "url": f"https://jobs.example.test/{i}"}).status_code == 200
    assert client.post("/jobs/paste", json={"text": PASTE.replace("Public", "Private")}).status_code == 200
    with sf() as s:  # two of the three become board jobs; the third stays a private paste
        s.execute(text("UPDATE jobs SET source = 'lever' WHERE company LIKE 'Board%'"))
        s.execute(text("INSERT INTO profiles (id, data, created_at, updated_at) "
                       "VALUES (gen_random_uuid(), '{}'::jsonb, now(), now())"))
        s.commit()

    seed = _seed_module()
    base = seed.libpq_url(db_url)
    server, target_db = base.rsplit("/", 1)[0], "joblens_seed_test"
    target = f"{server}/{target_db}"
    with psycopg.connect(f"{server}/postgres", autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {target_db}")
        c.execute(f"CREATE DATABASE {target_db}")
    try:
        assert seed.seed(db_url, target, "minilm", apply=False)["jobs"] == 2
        counts = seed.seed(db_url, target, "minilm", apply=True)
        assert counts["jobs"] == 2 and counts["job_embeddings"] == 4  # 2 jobs x 2 variants, MiniLM only
        with psycopg.connect(target) as t:
            assert t.execute("SELECT count(*) FROM jobs WHERE source = 'paste'").fetchone() == (0,)
            assert t.execute("SELECT DISTINCT model_name FROM job_embeddings").fetchall() == [("minilm",)]
            assert t.execute("SELECT count(*) FROM job_facts").fetchone() == (2,)
            assert t.execute("SELECT count(*) FROM profiles").fetchone() == (0,)
            assert t.execute("SELECT count(*) FROM jobs WHERE fts IS NOT NULL").fetchone() == (2,)
        with pytest.raises(SystemExit, match="already has 2 jobs"):
            seed.seed(db_url, target, "minilm", apply=True)
        with pytest.raises(SystemExit, match="same database"):
            seed.seed(db_url, db_url, "minilm", apply=True)
    finally:
        with psycopg.connect(f"{server}/postgres", autocommit=True) as c:
            c.execute(f"DROP DATABASE IF EXISTS {target_db} WITH (FORCE)")
