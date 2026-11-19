"""Review tool: labels, keep-draft, skip and quit; appends human_reviewed rows; never touches the drafts."""

import json

from app.schemas import Profile, SkillItem
from scripts.review_labels import review

PROFILE = Profile(degree="MCA", graduation_year=2027, skills=[SkillItem(name="python")], target_roles=["swe intern"])


def test_review_loop(tmp_path) -> None:  # type: ignore[no-untyped-def]
    items = [{"profile_id": "p00", "job_key": f"lever:{i}", "job_id": i, "title": f"Job {i}", "company": "C",
              "location": "Bengaluru", "summary": "x"} for i in range(5)]
    drafts = {("p00", f"lever:{i}"): {"relevance": 1, "reason": f"draft {i}", "status": "draft"} for i in range(5)}
    facts = {"lever:0": {"experience_min_years": {"value": 3, "evidence": "3+ years of Java"}}}
    out = tmp_path / "reviewed.jsonl"
    answers = iter(["2", "", "x", "s", "0", "q"])  # set 2, keep draft, invalid -> re-ask (s), set 0, quit
    shown: list[str] = []

    def ask(prompt: str) -> str:
        shown.append(prompt)
        return next(answers)

    counts = review(items, drafts, facts, {"p00": PROFILE}, out, ask)
    rows = [json.loads(ln) for ln in out.read_text(encoding="utf-8").splitlines()]
    assert counts == {"labelled": 2, "kept": 1, "skipped": 1}
    assert [(r["job_key"], r["relevance"], r["decision"]) for r in rows] == [
        ("lever:0", 2, "set"), ("lever:1", 1, "kept_draft"), ("lever:3", 0, "set")]
    assert all(r["status"] == "human_reviewed" and r["reviewed_at"] for r in rows)
    assert '<- "3+ years of Java"' in shown[0] and "Draft: relevance 1 - draft 0" in shown[0]
    assert drafts[("p00", "lever:0")]["relevance"] == 1  # drafts untouched

    # resume: already reviewed pairs are not shown again
    again = iter(["q"])
    review(items, drafts, facts, {"p00": PROFILE}, out, lambda p: shown.append(p) or next(again))
    assert "Job 2" in shown[-1]  # lever:0, 1, 3 were done; lever:2 was skipped, so it is first
