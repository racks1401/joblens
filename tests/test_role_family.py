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
