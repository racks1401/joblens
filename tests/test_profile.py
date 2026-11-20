import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.extract.profile import ResumeTextTooShort, extract_profile, pdf_to_text
from app.llm.cache import MemoryCache
from app.llm.client import LLMClient
from app.llm.fake import FakeProvider
from app.models import ChatMessage, ChatSession, UserMemory

from .pdfutil import RESUME_LINES, make_pdf

PROFILE = {
    "name": "Test User", "degree": "M.C.A", "specialization": "AI & IoT", "graduation_year": 2027, "cgpa": 8.4,
    "skills": [{"name": "Python"}, {"name": "JS"}, {"name": "python"}], "target_roles": ["Software Engineer Intern"],
    "preferred_locations": ["bangalore", "Gurugram"],
}


def test_pdf_to_text_roundtrip() -> None:
    t = pdf_to_text(make_pdf(RESUME_LINES))
    assert "MCA (AI & IoT)" in t and "scikit-learn" in t


def test_extract_profile_fake_llm() -> None:
    llm = LLMClient(FakeProvider(), "fake-heuristic", MemoryCache())
    d = extract_profile(pdf_to_text(make_pdf(RESUME_LINES)), llm)
    assert d.degree == "MCA" and d.graduation_year == 2027 and d.cgpa == 8.4
    names = [s.name for s in d.skills]
    assert {"python", "java", "sql", "flask", "streamlit", "scikit-learn", "firebase"} <= set(names)
    assert d.target_roles == []  # not in resume -> stays empty


def test_short_resume_rejected() -> None:
    with pytest.raises(ResumeTextTooShort):
        extract_profile("too short", LLMClient(FakeProvider(), "x", MemoryCache()))


@pytest.fixture
def client(sf) -> TestClient:  # type: ignore[no-untyped-def]
    from app.db import get_engine
    from app.main import create_app

    get_engine.cache_clear()
    return TestClient(create_app())


@pytest.mark.db
def test_from_resume_route(client: TestClient) -> None:
    r = client.post("/profiles/from-resume", files={"file": ("cv.pdf", make_pdf(RESUME_LINES), "application/pdf")})
    assert r.status_code == 200, r.text
    assert r.json()["draft"]["degree"] == "MCA"
    r2 = client.post("/profiles/from-resume", files={"file": ("cv.pdf", make_pdf(["Hi"]), "application/pdf")})
    assert r2.status_code == 422 and r2.json()["error"]["code"] == "resume_text_too_short"
    assert "resume_text" not in r.json() and "not stored" in r.json()["note"]
    r3 = client.post("/profiles/from-resume", files={"file": ("cv.pdf", b"not a pdf", "application/pdf")})
    assert r3.status_code == 415 and r3.json()["error"]["code"] == "not_a_pdf"
    r4 = client.post("/profiles/from-resume", files={"file": ("cv.pdf", b"%PDF-1.4 broken", "application/pdf")})
    assert r4.status_code == 400


@pytest.mark.db
def test_resume_text_never_stored_and_nothing_cached(client: TestClient, session) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import func, select

    from app.models import LLMCacheRow, ProfileRow

    client.post("/profiles/from-resume", files={"file": ("cv.pdf", make_pdf(RESUME_LINES), "application/pdf")})
    pid = client.post("/profiles", json={**PROFILE, "resume_text": "my private resume"}).json()["id"]
    assert session.get(ProfileRow, uuid.UUID(pid)).resume_text is None
    assert session.execute(select(func.count()).select_from(LLMCacheRow)).scalar() == 0


@pytest.mark.db
def test_demo_profile_is_synthetic_and_read_only(client: TestClient) -> None:
    from app.demo import DEMO_PROFILE_ID

    d = client.get("/profiles/demo").json()
    assert d["id"] == str(DEMO_PROFILE_ID) and "synthetic" in d["profile"]["name"]
    assert client.get("/profiles/demo").json()["id"] == d["id"]  # idempotent
    assert client.put(f"/profiles/{DEMO_PROFILE_ID}", json=PROFILE).status_code == 403
    assert client.delete(f"/profiles/{DEMO_PROFILE_ID}").status_code == 403


@pytest.mark.db
def test_profile_crud_and_cascade(client: TestClient, session) -> None:  # type: ignore[no-untyped-def]
    r = client.post("/profiles", json={**PROFILE, "resume_text": "resume"})
    assert r.status_code == 201, r.text
    body = r.json()
    pid = body["id"]
    assert body["profile"]["degree"] == "MCA"
    assert [s["name"] for s in body["profile"]["skills"]] == ["python", "javascript"]
    assert body["profile"]["preferred_locations"] == ["Bengaluru", "Gurugram"]
    assert client.get(f"/profiles/{pid}").json()["profile"]["cgpa"] == 8.4
    upd = client.put(f"/profiles/{pid}", json={**PROFILE, "cgpa": 9.0}).json()
    assert upd["profile"]["cgpa"] == 9.0 and upd["updated_at"] > body["updated_at"]

    sid = uuid.uuid4()
    session.add(ChatSession(id=sid, profile_id=uuid.UUID(pid)))
    session.flush()
    session.add(ChatMessage(session_id=sid, role="user", content={"text": "hi"}))
    session.add(UserMemory(profile_id=uuid.UUID(pid), key="notes", value="x"))
    session.commit()
    assert client.delete(f"/profiles/{pid}").status_code == 200
    session.expire_all()
    for model in (ChatSession, ChatMessage, UserMemory):
        assert session.execute(select(func.count()).select_from(model)).scalar() == 0
    assert session.execute(text("SELECT count(*) FROM profiles")).scalar() == 0
    assert client.get(f"/profiles/{pid}").status_code == 404


@pytest.mark.db
def test_profile_validation(client: TestClient) -> None:
    r = client.post("/profiles", json={"degree": "MCA"})
    assert r.status_code == 422
