"""Demo safety: per-session and per-day rate limits, token budgets, admin key, CORS, no secrets in logs."""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

PASTE = ("Role: Data Analyst Intern\nCompany: Limit Analytics\nLocation: Bengaluru\n"
         "Stipend: Rs 15,000 per month.\nSkills: Python, SQL.\nEligibility: MCA graduates of 2027 batch.")


@pytest.fixture
def client(sf):  # type: ignore[no-untyped-def]
    from app.db import get_engine
    from app.main import create_app

    get_engine.cache_clear()
    return TestClient(create_app())


def _limits(per_session: int, per_day: int) -> None:
    from app.config import ActionLimit, get_settings

    get_settings().env.demo_limits = True
    cfg = get_settings().cfg.limits
    cfg.enabled = True
    for a in ("chat", "paste", "explain", "resume"):
        cfg.actions[a] = ActionLimit(per_session=per_session, per_day=per_day)


@pytest.mark.db
def test_per_session_limit_then_clear_message(client: TestClient) -> None:
    _limits(per_session=2, per_day=100)
    h = {"X-Session-Id": "s1"}
    assert client.post("/jobs/paste", json={"text": PASTE}, headers=h).status_code == 200
    assert client.post("/jobs/paste", json={"text": PASTE}, headers=h).status_code == 200
    r = client.post("/jobs/paste", json={"text": PASTE}, headers=h)
    assert r.status_code == 429 and r.json()["error"]["code"] == "session_limit_reached"
    assert "2 paste requests" in r.json()["error"]["message"]
    # another session still has its own allowance
    assert client.post("/jobs/paste", json={"text": PASTE}, headers={"X-Session-Id": "s2"}).status_code == 200
    lim = client.get("/limits", headers=h).json()["actions"]["paste"]
    assert lim["session_used"] == 2 and lim["day_used"] == 3 and lim["per_session"] == 2


@pytest.mark.db
def test_per_day_limit_applies_across_sessions(client: TestClient) -> None:
    _limits(per_session=10, per_day=2)
    pid = client.get("/profiles/demo").json()["id"]
    for i in range(2):
        assert client.post("/chat", json={"profile_id": pid, "message": "data jobs"},
                           headers={"X-Session-Id": f"a{i}"}).status_code == 200
    r = client.post("/chat", json={"profile_id": pid, "message": "data jobs"}, headers={"X-Session-Id": "new"})
    assert r.status_code == 429 and r.json()["error"]["code"] == "daily_limit_reached"
    assert "resets at 00:00 UTC" in r.json()["error"]["message"]


@pytest.mark.db
def test_used_up_token_budget_refuses_with_message(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.api.limits as L

    _limits(per_session=10, per_day=10)
    monkeypatch.setattr(L, "budget_left", lambda purpose: (180001, 180000))
    pid = client.get("/profiles/demo").json()["id"]
    r = client.post("/chat", json={"profile_id": pid, "message": "hi"})
    assert r.status_code == 429 and r.json()["error"]["code"] == "daily_budget_used"
    assert "agent" in r.json()["error"]["message"]


def test_admin_route_needs_a_real_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.deps import require_admin
    from app.api.errors import APIError
    from app.config import get_settings

    with pytest.raises(APIError):
        require_admin(None)
    with pytest.raises(APIError):
        require_admin("wrong-key-123456")
    require_admin("test-admin-key")  # the configured key works
    get_settings().env.admin_api_key = "change-me"  # placeholder from .env.example: admin stays closed
    with pytest.raises(APIError):
        require_admin("change-me")


@pytest.mark.db
def test_admin_ingest_route_rejects_without_key(client: TestClient) -> None:
    assert client.post("/admin/ingest").status_code == 401
    assert client.post("/admin/ingest", headers={"X-API-Key": "nope"}).status_code == 401


@pytest.mark.db
def test_no_cors_headers_for_other_origins(client: TestClient) -> None:
    r = client.options("/chat", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}
    r = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}


@pytest.mark.db
def test_logs_hold_no_secrets_or_message_text(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    from app.config import get_settings

    caplog.set_level(logging.DEBUG)
    pid = client.get("/profiles/demo").json()["id"]
    secret_msg = "my phone is 98765 43210 and my resume says ZZQX-PRIVATE"
    client.post("/chat", json={"profile_id": pid, "message": secret_msg})
    client.post("/admin/ingest", headers={"X-API-Key": "wrong-key-guess"})
    text = "\n".join(r.getMessage() + " " + str(getattr(r, "__dict__", {})) for r in caplog.records)
    assert "ZZQX-PRIVATE" not in text and "98765" not in text
    assert "wrong-key-guess" not in text and get_settings().env.admin_api_key not in text
