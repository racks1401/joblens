"""Eval runner scoring (hand-computed) and harness behaviour without labels."""

import argparse

import pytest

from evals import run as R
from evals.common import labels_path, md_table

GOLD = {"employment_type": "internship", "work_mode": None, "duration_months": 6, "min_cgpa": None,
        "experience_min_years": None, "fresher_friendly": True, "apply_deadline": None, "ppo_or_conversion": None,
        "pay": {"min": 25000, "max": 25000, "currency": "INR", "period": "month"},
        "required_skills": ["Python", "SQL"], "nice_to_have_skills": None, "degrees_allowed": ["B.Tech", "MCA"],
        "locations": ["bangalore"], "graduation_years": [2026, 2027], "specializations": None}


def ev(v: object) -> dict[str, object]:
    return {"value": v, "evidence": "x"}


def test_score_extraction_hand_computed() -> None:
    pred = {"employment_type": ev("internship"), "work_mode": ev("remote"),  # hallucinated (gold null)
            "duration_months": ev(6.0), "fresher_friendly": ev(None),  # miss
            "pay": ev({"min": 25100, "max": 25100, "currency": "inr", "period": "month"}),  # within 1%
            "required_skills": ev(["python", "docker"]), "degrees_allowed": ev(["BTECH", "MCA"]),
            "locations": ev(["Bengaluru"]), "graduation_years": ev([2026, 2027])}
    m = R.score_extraction([{"gold": GOLD}], [pred], dropped=[1], valid=[True])
    fa, f1 = m["field_accuracy"], m["list_f1"]
    assert fa["employment_type"] == 1 and fa["work_mode"] == 0 and fa["duration_months"] == 1
    assert fa["fresher_friendly"] == 0 and fa["pay"] == 1 and fa["min_cgpa"] == 1  # null == null
    assert f1["required_skills"] == pytest.approx(0.5)  # {python,docker} vs {python,sql}
    assert f1["degrees_allowed"] == 1 and f1["locations"] == 1  # gold normalized before comparing
    # gold-null fields: work_mode, min_cgpa, experience, deadline, ppo, nice_to_have, specializations = 7; 1 hallucinated
    assert m["hallucination_rate"] == pytest.approx(1 / 7)
    assert m["evidence_grounded_rate"] == pytest.approx(8 / 9)  # 8 kept non-null, 1 dropped


def test_missing_labels_skip_cleanly() -> None:
    args = argparse.Namespace(labels="gold", prompt=None, model=None)
    assert "No labels found" in R.run_extraction(args)["skipped"]


def test_labels_path_and_table() -> None:
    assert labels_path("retrieval_labels.jsonl", "drafts").name == "retrieval_labels_draft.jsonl"
    assert labels_path("retrieval_labels.jsonl", "gold").parent.name == "data"
    assert md_table(["a", "b"], [[1.23456, None]]) == "| a | b |\n|---|---|\n| 1.235 | — |"


def test_extraction_suite_runs_on_fake_llm(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    import json

    from evals import common

    text = ("Title: Data Intern\n\nInternship for 6 months. Stipend Rs 20,000 per month. "
            "Requirements:\n- Python, SQL")
    f = tmp_path / "extraction_gold.jsonl"
    f.write_text(json.dumps({"job_key": "paste:1", "text": text, "gold": {**{k: None for k in R.FIELDS},
                 "employment_type": "internship", "duration_months": 6, "required_skills": ["python", "sql"],
                 "pay": {"min": 20000, "max": 20000, "currency": "INR", "period": "month"}}}) + "\n")
    monkeypatch.setattr(R, "labels_path", lambda name, labels: f)
    monkeypatch.setattr(common, "RESULTS", tmp_path)
    out = R.run_extraction(argparse.Namespace(labels="gold", prompt="facts_v1", model=None))
    m = out["metrics"]["fake-heuristic|facts_v1"]
    assert m["n"] == 1 and m["schema_valid_rate"] == 1 and m["field_accuracy"]["pay"] == 1
    assert m["list_f1"]["required_skills"] == 1 and "E2 Extraction (n = 1 jobs)" in out["markdown"]


@pytest.mark.db
def test_retrieval_suite_end_to_end(sf, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    import json

    from fastapi.testclient import TestClient

    from app.db import get_engine
    from app.main import create_app
    from evals import common

    from .test_search_db import JOBS, PROFILE

    get_engine.cache_clear()
    c = TestClient(create_app())
    keys = []
    for i, t in enumerate(JOBS):
        j = c.post("/jobs/paste", json={"text": t, "url": f"https://jobs.example.test/{i}"}).json()
        keys.append(f"paste:{j['id']}")
    with sf() as s:
        from app.models import Job

        keys = [f"{j.source}:{j.source_job_id}" for j in s.query(Job).order_by(Job.id)]
    prof = tmp_path / "profiles.jsonl"
    prof.write_text(json.dumps({"profile_id": "p01", "profile": PROFILE}) + "\n")
    labels = tmp_path / "retrieval_labels.jsonl"
    rel = [2, 0, 0, 2]  # python intern + ML intern relevant
    labels.write_text("".join(json.dumps({"profile_id": "p01", "job_key": k, "relevance": r}) + "\n"
                              for k, r in zip(keys, rel, strict=True)))
    monkeypatch.setattr(common, "DATA", tmp_path)
    monkeypatch.setattr(R, "labels_path", lambda name, lab: labels)
    monkeypatch.setattr(R, "load_profiles", lambda: common.load_profiles(prof))
    out = R.run_retrieval(argparse.Namespace(labels="gold"))
    mean = out["metrics"]["mean"]
    assert set(mean) == {"M1", "M2", "M3", "M4", "M5"}
    for m in mean.values():
        assert 0 <= m["ndcg@10"] <= 1 and 0 <= m["p@5"] <= 1
    assert out["metrics"]["best_vector"] in {"M2", "M3", "M4"} and "E1 Retrieval (n = 1 profiles" in out["markdown"]
