import pytest
from fastapi.testclient import TestClient


def _client() -> TestClient:
    from app.db import get_engine
    from app.main import create_app

    get_engine.cache_clear()
    return TestClient(create_app())


@pytest.mark.db
def test_health_green_with_db(db_url: str) -> None:
    r = _client().get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["db"]["ok"] and body["db"]["pgvector"]
    assert body["llm"]["provider"] == "fake"
    assert "x-request-id" in r.headers


def test_health_degraded_without_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://nobody@127.0.0.1:1/none")
    body = _client().get("/health").json()
    assert body["status"] == "degraded" and body["db"]["ok"] is False


def test_unknown_route_uses_error_shape() -> None:
    r = _client().get("/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "http_404"
