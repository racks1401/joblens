import pytest

from app.config import RankingCfg
from app.schemas import Evidenced, JobFacts, Profile, SkillItem
from app.search.hybrid import rrf
from app.search.rank import final_score, skill_overlap
from app.search.text import build_profile_query_text, profile_keyword_query, structured_text

CFG = RankingCfg()


def test_rrf_hand_computed() -> None:
    # A: ranks 1 and 2 -> 1/61 + 1/62; B: rank 2 and 1 -> same; C: rank 3 in list 1 only -> 1/63
    out = dict(rrf([[1, 2, 3], [2, 1]], k=60))
    a = 1 / 61 + 1 / 62
    assert out[1] == pytest.approx(1.0) and out[2] == pytest.approx(1.0)
    assert out[3] == pytest.approx((1 / 63) / a)
    assert [j for j, _ in rrf([[1, 2, 3], [2, 1]], k=60)] == [1, 2, 3]  # tie broken by first appearance
    assert rrf([], k=60) == [] and len(rrf([list(range(100))], top=50)) == 50


def test_skill_overlap_hand_computed() -> None:
    # required {python, sql, docker}, nice {aws, kubernetes}; profile has python, sql, aws
    # (2 + 0.5) / (3 + 1.0) = 0.625
    assert skill_overlap(["python", "sql", "aws"], ["python", "sql", "docker"], ["aws", "kubernetes"]) == 0.625
    assert skill_overlap(["python"], [], []) is None
    assert skill_overlap(["a", "b"], ["a"], ["b"]) == 1.0  # capped
    assert skill_overlap(["Python"], ["python"], None) == 1.0


def test_final_score_rules() -> None:
    assert final_score(1.0, 1.0, "ELIGIBLE", "ok", CFG) == 100
    assert final_score(0.8, 0.5, "ELIGIBLE", None, CFG) == round(100 * (0.65 * 0.8 + 0.35 * 0.5))  # 70
    assert final_score(0.8, None, "ELIGIBLE", None, CFG) == 80  # weight moves to retrieval
    assert final_score(0.8, None, "CHECK", None, CFG) == 75
    assert final_score(0.8, None, "ELIGIBLE", "risky", CFG) == 56
    assert final_score(0.8, None, "CHECK", "caution", CFG) == round(100 * 0.75 * 0.9)
    assert final_score(0.01, None, "CHECK", None, CFG) == 0  # clamped


def _profile() -> Profile:
    return Profile(degree="MCA", specialization="AI & IoT", graduation_year=2027,
                   skills=[SkillItem(name=s) for s in ["python", "java", "sql", "flask", "streamlit", "scikit-learn", "firebase"]],
                   target_roles=["software engineer intern", "ai engineer"], preferred_locations=["Bengaluru", "Gurugram"],
                   work_modes=["remote", "onsite"])


def test_profile_query_text_matches_spec_layout() -> None:
    t = build_profile_query_text(_profile())
    assert t.splitlines() == [
        "Target roles: software engineer intern, ai engineer",
        "Skills: python, java, sql, flask, streamlit, scikit-learn, firebase",
        "Degree: MCA (AI & IoT), graduating 2027",
        "Preferences: internship or full time; remote or onsite; Bengaluru, Gurugram",
    ]
    assert profile_keyword_query(_profile()).startswith("software engineer intern OR ai engineer OR python OR java")


def test_structured_text_layout() -> None:
    f = JobFacts(title="SWE Intern", employment_type=Evidenced(value="internship"), work_mode=Evidenced(value="remote"),
                 locations=Evidenced(value=["Bengaluru", "Gurugram"]), required_skills=Evidenced(value=["python", "sql"]),
                 nice_to_have_skills=Evidenced(value=["docker"]), degrees_allowed=Evidenced(value=["BTECH", "MCA"]),
                 graduation_years=Evidenced(value=[2026, 2027]), fresher_friendly=Evidenced(value=True))
    t = structured_text("Software Engineer Intern", "Example Co", "x" * 500, f)
    assert t.splitlines()[:6] == [
        "Title: Software Engineer Intern", "Company: Example Co",
        "Type: internship | Mode: remote | Locations: Bengaluru, Gurugram",
        "Required skills: python, sql", "Nice to have: docker",
        "Eligibility: degrees BTECH, MCA; batch 2026, 2027; fresher friendly",
    ]
    assert t.splitlines()[6] == "Summary: " + "x" * 400
