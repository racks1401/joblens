import pytest

from app.ingest.role_family import classify_role


@pytest.mark.parametrize("title,skills,family", [
    ("Software Engineer Intern", None, "engineering"),
    ("SDE I", None, "engineering"),
    ("Backend Developer", None, "engineering"),
    ("MTS - Performance Engineering", None, "engineering"),
    ("Forward Deployed Engineer", None, "engineering"),
    ("Data Analyst Intern", None, "data_ml"),
    ("Machine Learning Engineer", None, "data_ml"),
    ("AI Researcher Intern", None, "data_ml"),
    ("Associate Product Manager", None, "product"),
    ("Product Management Intern", None, "product"),
    ("Product Designer", None, "design"),
    ("UX Research Intern", None, "design"),
    ("Mid Market Account Executive", None, "other"),
    ("Customer Success Associate", None, "other"),
    ("Copywriting Intern", None, "other"),
    ("Talent Community", None, "other"),
    ("Solutions Engineer", None, "other"),
    ("Support Engineer I", None, "other"),
    ("Sales Engineer", None, "other"),
    ("Technical Account Manager", None, "other"),
    ("Graduate Trainee", ["python", "java", "sql"], "engineering"),
    ("Summer Intern", ["pandas", "machine learning"], "data_ml"),
    ("Summer Intern", ["excel"], "other"),
    ("Email Marketing Associate", ["python", "java"], "other"),  # not a generic title
])
def test_classify_role(title: str, skills: list[str] | None, family: str) -> None:
    assert classify_role(title, skills)[0] == family


@pytest.mark.parametrize("title,skills,family", [
    ("Benefit Verification Specialist - AI Agents (Healthcare)", ["payer portals", "cpt codes"], "other"),
    ("Embedded Devices Software Solution Sales - All Roles", None, "other"),
    ("Security Risk Management Specialist", None, "other"),
    ("Founder's Office: Revenue Operations & Systems (RevOps)", None, "other"),
    ("Training Content Developer", None, "other"),
    ("Business Systems Analyst, ServiceNow", ["servicenow", "itsm processes"], "other"),  # weak 'systems', no tech skill
    ("Global People Operations - Systems Administrator", None, "other"),
    ("Software Engineer - L3 Support", None, "engineering"),     # head decides
    ("Sales Engineer", None, "other"),
    ("Software Engineer, Sales Platform", None, "engineering"),  # head names an engineer
    ("AI Engineer - Agents", ["python", "llm"], "data_ml"),
    ("Platform Engineer", [], "engineering"),                    # no skills extracted -> no veto
])
def test_role_family_fix(title: str, skills: list[str] | None, family: str) -> None:
    assert classify_role(title, skills)[0] == family


def test_overrides_win(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings
    from app.ingest.role_family import role_for_job

    get_settings().cfg.ingest.role_family_overrides = {"lever:x": {"family": "data_ml", "reason": "reviewed"}}
    assert role_for_job("lever:x", "AI Product Builder", ["product sense"]) == ("data_ml", "override: reviewed")
    assert role_for_job("lever:y", "AI Product Builder", ["product sense"])[0] == "other"
