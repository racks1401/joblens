import pytest

from app.config import IngestCfg
from app.ingest.common import join_locations, parse_dt
from app.ingest.normalize import content_hash, html_to_text, location_matches
from app.ingest.paste import from_paste, guess_title

CFG = IngestCfg(india_keywords=["india", "bengaluru", "gurugram"], remote_keywords=["remote", "anywhere"],
                remote_exclude=["us", "united states", "europe"])


def test_html_to_text_lists_and_whitespace() -> None:
    html = "<h2>About</h2><p>We   build\n things.</p><ul><li>Python</li><li>SQL &amp; APIs</li></ul><script>x()</script>"
    assert html_to_text(html) == "About\n\nWe build things.\n\n- Python\n- SQL & APIs"


def test_html_to_text_escaped_greenhouse_content() -> None:
    assert html_to_text("&lt;p&gt;Hello &amp;amp; bye&lt;/p&gt;") == "Hello & bye"


def test_html_to_text_caps_length() -> None:
    assert len(html_to_text("<p>" + "a" * 30000 + "</p>")) == 20000
    assert html_to_text(None) == ""


def test_content_hash_rule() -> None:
    import hashlib

    assert content_hash("Acme", "SWE Intern", "desc") == hashlib.sha256(b"acme|swe intern|desc").hexdigest()
    assert content_hash("ACME", "swe intern", "desc") == content_hash("acme", "SWE INTERN", "desc")
    assert content_hash("acme", "swe intern", "desc!") != content_hash("acme", "swe intern", "desc")


def test_location_filter() -> None:
    assert location_matches("Bengaluru, Karnataka, India", CFG)
    assert location_matches("Remote", CFG)
    assert location_matches("Remote - India", CFG)
    assert not location_matches("Remote - US", CFG)
    assert not location_matches("San Francisco, CA", CFG)
    assert not location_matches(None, CFG)
    assert not location_matches("Remote (Europe)", CFG)
    assert location_matches("anything", IngestCfg())  # empty config keeps all


def test_join_locations_and_parse_dt() -> None:
    assert join_locations("Pune", "pune", None, "Remote") == "Pune / Remote"
    assert join_locations("Pune", remote=True) == "Pune / Remote"
    assert join_locations(None) is None
    assert parse_dt(1700000000000).year == 2023  # epoch ms (Lever)
    assert parse_dt("2026-01-02T03:04:05Z").tzinfo is not None
    assert parse_dt("not a date") is None


def test_paste_job() -> None:
    nj = from_paste("Role: Data Analyst Intern\nStipend 15k/month. Apply on WhatsApp.", None, None)
    assert nj.source == "paste" and nj.title == "Data Analyst Intern" and nj.company == ""
    nj2 = from_paste("Some job text here for testing", "https://x.example/j/1", "Acme")
    assert nj2.source_job_id == from_paste("other text entirely here", "https://x.example/j/1").source_job_id
    assert guess_title("\n\n  ## Backend Engineer\nmore") == "Backend Engineer"


@pytest.mark.parametrize("loc,ok", [
    ("Remote - India", True), ("Remote", True), ("Remote, Global", True), ("Global / Remote", True),
    ("Home based - Worldwide", True), ("Remote - APAC", True), ("Anywhere", True), ("Delhi HQ", True),
    ("Remote - EMEA / Remote - India", True),
    ("Remote - US", False), ("Remote (Europe)", False), ("Vienna, Austria / Remote", False),
    ("Florida / Florida Remote", False), ("Remote, Georgia; Remote, Tennessee", False),
    ("Washington DC / Remote", False), ("Sweden (Remote)", False), ("Berlin", False), (None, False),
])
def test_india_eligibility(loc: str | None, ok: bool) -> None:
    from app.config import get_settings
    from app.ingest.normalize import india_eligibility

    el, reason = india_eligibility(loc, get_settings().cfg.ingest)
    assert el is ok and reason


def test_paste_company_line_is_company_not_title() -> None:
    text = "Company: Elip\n\nSoftware Engineer\nJoin Elip, a YC startup.\n\nPay: 1.5 lakh a month\nType: Internship"
    nj = from_paste(text)
    assert nj.company == "Elip" and nj.title == "Software Engineer"
    spec = "Company: Acme\n\nCompany's flagship products:\n* DefXV: AR system\n\nWHO CAN APPLY\n* Degrees: B.Tech\n"
    assert from_paste(spec).title != "Company: Acme" and not from_paste(spec).title.startswith(("Company", "*", "WHO"))
    assert from_paste("Company: X\nRole: Data Intern\nmore text here").title == "Data Intern"
