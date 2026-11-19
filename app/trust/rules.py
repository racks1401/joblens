"""Scam rules (spec 13). Each rule returns a TrustReason or None. Deltas come from settings.

These are the only checks allowed to say a posting looks like a scam: a rule fired, with its evidence.
"""

from __future__ import annotations

import re
from typing import Any

from app.eligibility import monthly_inr
from app.extract.heuristic import RED_FLAG_PATTERNS, first_unnegated
from app.schemas import JobFacts, TrustReason

FREE_EMAIL = re.compile(r"\b[\w.+-]+@(gmail|googlemail|yahoo|ymail|outlook|hotmail|live|rediffmail|aol|proton(?:mail)?)"
                        r"\.(?:com|co\.in|in|me)\b", re.I)
URL = re.compile(r"https?://[^\s)>\]]+|\bwww\.[^\s)>\]]+", re.I)
MESSENGER_URL = re.compile(r"(?i)(?:wa\.me|chat\.whatsapp\.com|t\.me|telegram\.me|api\.whatsapp\.com)")
EARN_PER_DAY = re.compile(r"(?i)\bearn\s+(?:up to\s+)?(?:rs\.?|₹|inr)?\s*[\d,]+\s*k?\s*(?:per|/|a|every)\s*(?:day|week)\b|"
                          r"\bwork from home and earn\b")
_MONEY_PAT = dict(RED_FLAG_PATTERNS)["asks for a fee or deposit"]
_CONTACT_PAT = dict(RED_FLAG_PATTERNS)["contact only via WhatsApp/Telegram"]


def _snip(text: str, m: re.Match[str]) -> str:
    return text[max(0, m.start() - 30): m.end() + 30].replace("\n", " ").strip()


def money_request(text: str, facts: JobFacts | None, delta: int) -> TrustReason | None:
    """Registration/security/training fee or deposit, or payment to get the job (regex + extracted red flags)."""
    m = first_unnegated(_MONEY_PAT, text)
    if m:
        return TrustReason(code="money", message=f"Asks for money: \"{_snip(text, m)}\"", delta=delta)
    for rf in (facts.red_flags if facts else []):
        if rf.value and re.search(r"(?i)fee|deposit|pay|money|charge", rf.value):
            return TrustReason(code="money", message=f"Asks for money (extracted): \"{rf.evidence}\"", delta=delta)
    return None


def offplatform_only(text: str, url: str | None, delta: int) -> TrustReason | None:
    """Contact only via WhatsApp/Telegram, with no company site or ATS link."""
    m = first_unnegated(_CONTACT_PAT, text)
    if not m:
        return None
    other_links = [u for u in URL.findall(text) if not MESSENGER_URL.search(u)]
    if url or other_links:
        return None
    return TrustReason(code="offplatform", message=f"Contact only via WhatsApp/Telegram, no company or ATS link: "
                       f"\"{_snip(text, m)}\"", delta=delta)


def free_email(text: str, company: str, delta: int) -> TrustReason | None:
    """Recruiter email on a free provider for a company role."""
    m = FREE_EMAIL.search(text)
    if not m:
        return None
    return TrustReason(code="free_email", message=f"Recruiter email on a free provider ({m.group(0)})"
                       + (f" for {company}" if company else ""), delta=delta)


def unrealistic_pay(text: str, facts: JobFacts | None, max_intern_month: int, delta: int) -> TrustReason | None:
    """Internship over the configured monthly cap, or 'earn X per day from home' phrasing."""
    m = EARN_PER_DAY.search(text)
    if m:
        return TrustReason(code="unrealistic_pay", message=f"Earn-per-day promise: \"{_snip(text, m)}\"", delta=delta)
    if facts and facts.employment_type.value == "internship" and facts.pay.value:
        inr = monthly_inr(facts.pay.value, facts.duration_months.value)
        if inr is not None and inr > max_intern_month:
            return TrustReason(code="unrealistic_pay", message=f"Internship pay ~₹{inr:,.0f}/month is above "
                               f"₹{max_intern_month:,}/month: \"{facts.pay.evidence}\"", delta=delta)
    return None


def missing_company(company: str, url: str | None, delta: int, pasted: bool = False) -> TrustReason | None:
    """No company name or no URL (spec 13). Pasted jobs have no job URL by nature, so only the company counts."""
    checks = [("company name", bool(company and company.strip()))] + ([] if pasted else [("job URL", bool(url))])
    missing = [w for w, ok in checks if not ok]
    if not missing:
        return None
    return TrustReason(code="missing_company", message="No " + " and no ".join(missing), delta=delta)


def run_rules(text: str, company: str, url: str | None, facts: JobFacts | None, cfg: Any,
              pasted: bool = False) -> list[TrustReason]:
    d = cfg.deltas
    out = [money_request(text, facts, d["money"]), offplatform_only(text, url, d["offplatform"]),
           free_email(text, company, d["free_email"]),
           unrealistic_pay(text, facts, cfg.unrealistic_intern_pay_inr_month, d["unrealistic_pay"]),
           missing_company(company, url, d["missing_company"], pasted)]
    return [r for r in out if r]
