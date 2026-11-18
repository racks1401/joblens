import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.db

PASTE = (
    "Role: Data Analyst Intern\nCompany: Example Analytics\nLocation: Pune\n"
    "Stipend: Rs 15,000 per month. Duration: 6 months internship.\nSkills: Python, SQL, Excel, Power BI.\n"
    "Eligibility: B.Tech / BCA / B.Sc graduates of 2025 and 2026 batch. Freshers can apply."
)


@pytest.fixture
def client(sf) -> TestClient:  # type: ignore[no-untyped-def]
    from app.db import get_engine
    from app.main import create_app

    get_engine.cache_clear()
    return TestClient(create_app())


def test_paste_then_get(client: TestClient) -> None:
    r = client.post("/jobs/paste", json={"text": PASTE, "url": "https://example.test/j/1"})
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["source"] == "paste" and job["title"] == "Data Analyst Intern"
    r2 = client.get(f"/jobs/{job['id']}")
    assert r2.status_code == 200 and r2.json()["id"] == job["id"]
    # same URL again -> same job id
    assert client.post("/jobs/paste", json={"text": PASTE, "url": "https://example.test/j/1"}).json()["id"] == job["id"]


def test_get_missing_job_404(client: TestClient) -> None:
    r = client.get("/jobs/999999")
    assert r.status_code == 404 and r.json()["error"]["code"] == "job_not_found"


def test_paste_validation_error(client: TestClient) -> None:
    r = client.post("/jobs/paste", json={"text": "short"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"


def test_admin_ingest_requires_key(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    assert client.post("/admin/ingest").status_code == 401
    assert client.post("/admin/ingest", headers={"X-API-Key": "wrong"}).status_code == 401
    import app.api.routes_jobs as rj

    monkeypatch.setattr(rj, "_ingest_task", lambda: None)
    assert client.post("/admin/ingest", headers={"X-API-Key": "test-admin-key"}).status_code == 202
