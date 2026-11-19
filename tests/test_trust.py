"""Trust rules, ghost signals, score/levels and the classifier training script (tiny synthetic data)."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.schemas import Evidenced, JobFacts, Money
from app.trust import rules
from app.trust.ghost import listing_age, reposted
from app.trust.score import compute_trust, level_for, scam_rules_fired

NOW = datetime(2026, 11, 19, tzinfo=UTC)


def job(text: str, company: str = "Acme", url: str | None = "https://acme.test/jobs/1", posted_days: int = 5,
        reposts: int = 0, title: str = "Software Engineer") -> SimpleNamespace:
    return SimpleNamespace(title=title, description_text=text, company=company, url=url,
                           posted_at=NOW - timedelta(days=posted_days), first_seen_at=NOW, repost_count=reposts)


@pytest.mark.parametrize("text,fires", [
    ("Pay a registration fee of Rs 499 to confirm your seat.", True),
    ("A refundable security deposit of INR 2,000 is required.", True),
    ("We will never ask you to pay a fee or deposit.", False),
    ("Beware of fraudsters asking for a registration fee.", False),
])
def test_money_rule(text: str, fires: bool) -> None:
    assert (rules.money_request(text, None, -25) is not None) is fires


def test_offplatform_requires_no_company_or_ats_link() -> None:
    t = "Send your CV on WhatsApp to 9876543210."
    assert rules.offplatform_only(t, None, -15) is not None
    assert rules.offplatform_only(t, "https://acme.test/careers", -15) is None
    assert rules.offplatform_only(t + " Apply at https://acme.test/apply", None, -15) is None
    assert rules.offplatform_only(t + " Join https://t.me/scamgroup", None, -15) is not None  # messenger link only


def test_free_email_unrealistic_pay_missing_company() -> None:
    assert rules.free_email("Mail hr.acme@gmail.com", "Acme", -10) is not None
    assert rules.free_email("Mail careers@acme.com", "Acme", -10) is None
    assert rules.unrealistic_pay("Earn Rs 3000 per day from home!", None, 200000, -10) is not None
    intern = JobFacts(title="T", employment_type=Evidenced(value="internship", evidence="x"),
                      pay=Evidenced(value=Money(min=250000, max=250000, currency="INR", period="month"), evidence="₹2.5L/month"))
    assert rules.unrealistic_pay("", intern, 200000, -10) is not None
    assert rules.missing_company("", "https://x.test", -10).message == "No company name"  # type: ignore[union-attr]
    assert rules.missing_company("Acme", None, -10).message == "No job URL"  # type: ignore[union-attr]
    assert rules.missing_company("Acme", "https://x.test", -10) is None


def test_ghost_signals() -> None:
    d = get_settings().cfg.trust.deltas
    assert listing_age(NOW - timedelta(days=61), None, NOW, d).code == "age_60"  # type: ignore[union-attr]
    assert listing_age(NOW - timedelta(days=121), None, NOW, d).code == "age_120"  # type: ignore[union-attr]
    assert listing_age(NOW - timedelta(days=10), None, NOW, d) is None
    assert listing_age(None, NOW - timedelta(days=70), NOW, d).code == "age_60"  # type: ignore[union-attr]
    assert reposted(2, 2, -10) is not None and reposted(1, 2, -10) is None


def test_score_levels_and_scam_rules_only_from_rules() -> None:
    lv = get_settings().cfg.trust.levels
    assert [level_for(s, lv) for s in (100, 70, 69, 40, 39)] == ["ok", "ok", "caution", "caution", "risky"]
    clean = compute_trust(job("Build APIs with Python."), None, NOW, use_classifier=False)
    assert clean.score == 100 and clean.level == "ok" and scam_rules_fired(clean) == []
    scam = compute_trust(job("Earn Rs 3000 per day from home. Pay a registration fee of Rs 499. WhatsApp: +91 90000 12345",
                             company="", url=None, posted_days=130), None, NOW, use_classifier=False)
    codes = {r.code for r in scam.reasons}
    assert codes == {"age_120", "money", "offplatform", "unrealistic_pay", "missing_company"}
    assert scam.score == 100 - 30 - 25 - 15 - 10 - 10 and scam.level == "risky"
    assert {r.code for r in scam_rules_fired(scam)} == {"money", "offplatform", "unrealistic_pay"}


def test_classifier_delta_is_capped() -> None:
    class Fixed:
        def predict_proba(self, xs):  # type: ignore[no-untyped-def]
            return [[0.0, 1.0] for _ in xs]

    t = compute_trust(job("Build APIs."), None, NOW, model=Fixed())
    assert t.scam_prob == 1.0 and t.score == 60 and t.level == "caution"  # -40 max, no rule fired
    assert scam_rules_fired(t) == []


def test_training_script_on_tiny_synthetic_sample() -> None:
    from ml.train_scam_classifier import train

    real = [f"Software engineer role {i} building python services with our team" for i in range(40)]
    fake = [f"Earn money from home {i} pay registration fee whatsapp now" for i in range(40)]
    model, m = train(real + fake, [0] * 40 + [1] * 40, min_df=1, cv=3)
    assert m["split"]["test"] == 8 and m["test_at_val_threshold"]["f1"] == 1.0
    assert model.predict_proba(["pay registration fee whatsapp"])[0][1] > 0.5


def test_pasted_jobs_not_penalised_for_missing_url() -> None:
    pasted = job("Build APIs with Python.", company="Acme", url=None)
    pasted.source = "paste"
    t = compute_trust(pasted, None, NOW, use_classifier=False)
    assert t.score == 100 and t.reasons == []
    pasted.company = ""
    t2 = compute_trust(pasted, None, NOW, use_classifier=False)
    assert [r.message for r in t2.reasons] == ["No company name"]
    board = job("Build APIs with Python.", company="Acme", url=None)
    board.source = "lever"
    assert [r.message for r in compute_trust(board, None, NOW, use_classifier=False).reasons] == ["No job URL"]
