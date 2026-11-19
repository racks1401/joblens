"""Search + matching end to end on the test DB: paste jobs -> pipeline (fake LLM, fake embedder) -> /search, /matches."""

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.db

JOBS = [
    ("Role: Python Backend Intern\nCompany: Alpha\nLocation: Bengaluru\nInternship, onsite. Stipend Rs 30,000 per month.\n"
     "Eligibility: B.Tech / MCA, 2026 and 2027 batch.\nRequirements:\n- Python, SQL, REST APIs, Flask"),
    ("Role: Android Developer\nCompany: Beta\nLocation: Pune\nFull-time role. 3+ years of experience in Android.\n"
     "Requirements:\n- Kotlin, Android, Jetpack Compose"),
    ("Role: Senior Data Engineer\nCompany: Gamma\nLocation: Bengaluru\nFull-time. 6+ years of experience required.\n"
     "Requirements:\n- Python, Spark, Airflow, SQL"),
    ("Role: Machine Learning Intern\nCompany: Delta\nLocation: Remote\nRemote internship for final year students.\n"
     "Requirements:\n- Python, scikit-learn, pandas, machine learning"),
]
PROFILE = {
    "degree": "MCA", "specialization": "AI & IoT", "graduation_year": 2027,
    "skills": [{"name": s} for s in ["python", "java", "sql", "flask", "streamlit", "scikit-learn", "firebase"]],
    "target_roles": ["software engineer intern", "ai engineer"], "preferred_locations": ["Bengaluru", "Gurugram"],
    "work_modes": ["remote", "onsite"],
}


@pytest.fixture
def client(sf) -> TestClient:  # type: ignore[no-untyped-def]
    from app.db import get_engine
    from app.main import create_app

    get_engine.cache_clear()
    c = TestClient(create_app())
    for i, t in enumerate(JOBS):
        r = c.post("/jobs/paste", json={"text": t, "url": f"https://jobs.example.test/{i}", "company": t.split("Company: ")[1].split("\n")[0]})
        assert r.status_code == 200, r.text
    return c


def test_pipeline_wrote_facts_embeddings_search_text(client: TestClient, session) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import text

    assert session.execute(text("SELECT count(*) FROM job_embeddings")).scalar() == len(JOBS) * 4  # 2 models x 2 variants
    st = session.execute(text("SELECT search_text FROM jobs ORDER BY id LIMIT 1")).scalar()
    assert st.startswith("Title: Python Backend Intern") and "Required skills: python, sql, rest apis, flask" in st


@pytest.mark.parametrize("mode", ["keyword", "vector", "hybrid"])
def test_search_modes(client: TestClient, mode: str) -> None:
    # the Android job asks for 3+ years -> not fresher-relevant: hidden by default, found with include_senior
    hidden = client.get("/search", params={"q": "android kotlin", "mode": mode, "limit": 5}).json()["results"]
    assert "Android Developer" not in [x["title"] for x in hidden]
    r = client.get("/search", params={"q": "android kotlin", "mode": mode, "limit": 5, "include_senior": True})
    assert r.status_code == 200, r.text
    res = r.json()["results"]
    assert res and res[0]["title"] == "Android Developer"


def test_search_filters_and_model_variant(client: TestClient) -> None:
    r = client.get("/search", params={"q": "python", "mode": "keyword", "employment_type": "internship"}).json()
    assert {x["title"] for x in r["results"]} == {"Python Backend Intern", "Machine Learning Intern"}
    r2 = client.get("/search", params={"q": "python", "mode": "vector", "model": "bge-small", "variant": "raw"})
    assert r2.status_code == 200 and r2.json()["results"]
    assert client.get("/search", params={"q": "x", "model": "nope"}).status_code == 400


def test_matches_rank_and_hide_ineligible(client: TestClient) -> None:
    pid = client.post("/profiles", json=PROFILE).json()["id"]
    body = client.get(f"/matches/{pid}").json()
    titles = [r["title"] for r in body["results"]]
    assert "Senior Data Engineer" not in titles and "Android Developer" not in titles  # experience FAIL -> hidden
    assert titles[0] in {"Python Backend Intern", "Machine Learning Intern"}
    top = body["results"][0]
    assert 0 <= top["final_score"] <= 100 and top["eligibility"]["rules"]
    assert all("message" in r for r in top["eligibility"]["rules"])
    assert {r["title"] for r in client.get(f"/matches/{pid}", params={"show_ineligible": True}).json()["results"]}.isdisjoint(
        {"Senior Data Engineer", "Android Developer"})  # senior roles excluded unless include_senior
    full = client.get(f"/matches/{pid}", params={"show_ineligible": True, "include_senior": True}).json()["results"]
    assert {r["title"] for r in full} >= {"Senior Data Engineer"}
    assert any(r["eligibility"]["verdict"] == "INELIGIBLE" for r in full)
