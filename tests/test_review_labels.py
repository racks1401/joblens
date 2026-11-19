"""Review tool: labels, keep-draft, skip and quit; appends human_reviewed rows; never touches the drafts;
shows warnings, raw posting text and year/experience lines."""

import json

import pytest

from app.schemas import Profile, SkillItem
from scripts.review_labels import load_jobs, review, year_lines

PROFILE = Profile(degree="MCA", graduation_year=2027, skills=[SkillItem(name="python")], target_roles=["swe intern"])
TEXT = "About us.\nRequired Skills and Experience:\n2+ years of professional experience in ML.\nPerks: lunch."


def test_review_loop(tmp_path) -> None:  # type: ignore[no-untyped-def]
    items = [{"profile_id": "p00", "job_key": f"lever:{i}", "job_id": i, "title": f"Job {i}", "company": "C",
              "location": "Bengaluru", "summary": "short summary"} for i in range(5)]
    drafts = {("p00", f"lever:{i}"): {"relevance": 1, "reason": f"draft {i}", "status": "draft"} for i in range(5)}
    jobs = {"lever:0": {"facts": {"experience_min_years": {"value": 3, "evidence": "3+ years of Java"}}, "text": TEXT},
            "lever:1": {"facts": None, "text": TEXT}, "lever:2": {"facts": {}, "text": TEXT}, "lever:3": {"facts": {}, "text": ""}}
    out = tmp_path / "reviewed.jsonl"
    answers = iter(["2", "", "x", "s", "0", "q"])  # set 2, keep draft, invalid -> re-ask (s), set 0, quit
    shown: list[str] = []

    def ask(prompt: str) -> str:
        shown.append(prompt)
        return next(answers)

    counts = review(items, drafts, jobs, {"p00": PROFILE}, out, ask)
    rows = [json.loads(ln) for ln in out.read_text(encoding="utf-8").splitlines()]
    assert counts == {"labelled": 2, "kept": 1, "skipped": 1}
    assert [(r["job_key"], r["relevance"], r["decision"], r["shown"]) for r in rows] == [
        ("lever:0", 2, "set", "facts"), ("lever:1", 1, "kept_draft", "raw_posting"), ("lever:3", 0, "set", "raw_posting")]
    assert all(r["status"] == "human_reviewed" and r["reviewed_at"] for r in rows)
    assert '<- "3+ years of Java"' in shown[0] and "> 2+ years of professional experience in ML." in shown[0]
    assert "NO EXTRACTED FACTS" in shown[1] and "Raw posting (first 1500 chars)" in shown[1] and "Perks: lunch." in shown[1]
    assert drafts[("p00", "lever:0")]["relevance"] == 1  # drafts untouched

    # resume: already reviewed pairs are not shown again
    again = iter(["q"])
    review(items, drafts, jobs, {"p00": PROFILE}, out, lambda p: shown.append(p) or next(again))
    assert "Job 2" in shown[-1]


def test_job_missing_from_db_is_flagged(tmp_path) -> None:  # type: ignore[no-untyped-def]
    items = [{"profile_id": "p00", "job_key": "lever:9", "title": "Gone", "company": "C", "summary": "only this"}]
    shown: list[str] = []
    review(items, {("p00", "lever:9"): {"relevance": 0, "reason": "r"}}, {}, {"p00": PROFILE}, tmp_path / "o.jsonl",
           lambda p: shown.append(p) or "q")
    assert "JOB NOT FOUND IN THE DATABASE" in shown[0] and "only this" in shown[0]


def test_year_lines() -> None:
    assert year_lines(TEXT) == ["Required Skills and Experience:", "2+ years of professional experience in ML."]


def test_load_jobs_reports_db_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.db import get_engine

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://nobody@127.0.0.1:1/none")
    get_engine.cache_clear()
    jobs, err, label = load_jobs(["lever:1"])
    get_engine.cache_clear()
    assert jobs == {} and err and label == "127.0.0.1:1/none"
