import pytest

from app.ingest.seniority import classify, min_years_required


@pytest.mark.parametrize("title,desc,relevant", [
    ("Senior Software Engineer", "", False),
    ("Sr. Data Analyst", "", False),
    ("Staff Engineer", "", False),
    ("Principal Architect", "", False),
    ("Engineering Manager", "", False),
    ("Director, Product", "", False),
    ("Head of Growth", "", False),
    ("Tech Lead - Payments", "", False),
    ("Solutions Architect", "", False),
    ("Software Engineer", "We need 5+ years of experience with Java.", False),
    ("Data Engineer", "Minimum 3 years of hands-on experience.", False),
    ("Backend Engineer", "3-5 years of experience in Python.", False),
    ("Software Engineer", "1-2 years of experience preferred.", True),
    ("Software Engineer", "Build APIs with Python.", True),
    ("Software Engineer Intern", "Interns with 3+ years of open source experience welcome", True),  # junior title wins
    ("Product Manager Intern", "", True),
    ("Associate Product Manager", "", True),
    ("Graduate Solutions Architect", "", True),
    ("Senior Intern", "", False),  # hard seniority word is never overridden
    ("Leadership Development Program Trainee", "", True),  # 'leadership' is not 'lead'
    ("Software Engineer III", "", False),
    ("SDE3, Infrastructure Security", "", False),
    ("SDE 2 Infra", "", True),
    ("Support Engineer I", "", True),
])
def test_classify(title: str, desc: str, relevant: bool) -> None:
    assert classify(title, desc)[0] is relevant


def test_min_years() -> None:
    assert min_years_required("2+ years experience; ideally 5+ years of experience") == 2
    assert min_years_required("founded 10+ years ago") is None  # no 'experience' nearby
    assert min_years_required("") is None
