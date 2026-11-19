"""HTML -> text, whitespace cleanup, content hash and the India/remote location filter (spec 7.3)."""

from __future__ import annotations

import hashlib
import html
import re
from html.parser import HTMLParser

from app.config import IngestCfg

MAX_CHARS = 20000
_BLOCK = {"p", "div", "br", "ul", "ol", "tr", "table", "section", "h1", "h2", "h3", "h4", "h5", "h6", "hr"}
_SKIP = {"script", "style", "head", "noscript"}


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP:
            self.skip += 1
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in _BLOCK and not (self.parts and self.parts[-1] == "\n- "):
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(re.sub(r"\s*\n\s*", " ", data))  # newlines inside HTML text are spaces


def clean_whitespace(text: str) -> str:
    """Collapse runs of spaces per line, strip lines, drop blank-line runs."""
    lines = [re.sub(r"[ \t\r\f\v ]+", " ", ln).strip() for ln in text.split("\n")]
    out: list[str] = []
    for ln in lines:
        if ln == "-":
            continue
        if ln or (out and out[-1]):
            out.append(ln)
    return "\n".join(out).strip()


def html_to_text(raw: str | None, max_chars: int = MAX_CHARS) -> str:
    """Strip tags; list items become lines starting with '- '; collapse whitespace; cap length.

    Handles entity-escaped HTML (Greenhouse returns `&lt;p&gt;...`).
    """
    if not raw:
        return ""
    if "&lt;" in raw and "<" not in raw:
        raw = html.unescape(raw)
    p = _TextParser()
    p.feed(raw)
    p.close()
    text = clean_whitespace("".join(p.parts))
    text = re.sub(r"^(- .*)\n\n(?=- )", r"\1\n", text, flags=re.M)  # no blank lines inside a list
    return text[:max_chars]


def norm_key(s: str) -> str:
    """Lowercase + single spaces: used for company/title comparisons."""
    return re.sub(r"\s+", " ", s.strip().lower())


def content_hash(company: str, title: str, description_text: str) -> str:
    """sha256(lower(company) + '|' + lower(title) + '|' + description_text)."""
    return hashlib.sha256(f"{company.lower()}|{title.lower()}|{description_text}".encode()).hexdigest()


def _has_word(text: str, word: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", text) is not None


# Words that say nothing about WHERE a remote job is open ("Remote, Global", "Fully remote - anywhere").
GENERIC_REMOTE = {"remote", "anywhere", "worldwide", "global", "globally", "fully", "first", "work", "from", "home",
                  "wfh", "any", "location", "locations", "distributed", "flexible", "apac", "asia", "pacific",
                  "asia-pacific", "the", "in", "or", "and", "based", "home-based", "home based"}


def india_eligibility(location: str | None, cfg: IngestCfg) -> tuple[bool, str]:
    """(open to candidates in India, reason).

    Location strings are split into parts (" / ", ";", "|"). Eligible if any part names an Indian place,
    or if EVERY part is a generic remote term ("Remote", "Global", "Anywhere", "APAC"). A remote listing that
    sits next to, or is qualified by, another place ("Vienna, Austria / Remote", "Remote - US",
    "Remote (Europe)") is not India-eligible: the remote option is limited to that country or region.
    """
    if not cfg.india_keywords and not cfg.remote_keywords:
        return True, "no location filter configured"
    if not location or not location.strip():
        return False, "no location"
    loc = location.lower()
    if any(_has_word(loc, k) for k in cfg.india_keywords):
        return True, "India location"
    parts = [p.strip() for p in re.split(r"\s*(?:/|;|\|)\s*", loc) if p.strip()]
    if not any(_has_word(loc, k) for k in cfg.remote_keywords):
        return False, f"not India and not remote: {location.strip()[:60]}"
    others = []
    for p in parts:
        words = [w for w in re.split(r"[\s,()\-–:.]+", p) if w]
        rest = [w for w in words if w not in GENERIC_REMOTE]
        if rest:
            others.append(" ".join(rest))
    if not others:
        return True, "open remote"
    return False, f"remote limited to {', '.join(dict.fromkeys(others))[:60]}"


def location_matches(location: str | None, cfg: IngestCfg) -> bool:
    """True if a job is open to candidates in India (see india_eligibility)."""
    return india_eligibility(location, cfg)[0]


# Location phrases inside the posting text. Each pattern captures the place it names.
_PLACE = r"([A-Za-z][A-Za-z .,&/()'-]{1,60})"
TEXT_LOCATION_PATTERNS = [
    re.compile(r"^\s*(?:[-*•]\s*)?(?:job\s+|work\s+|office\s+)?locations?\s*[:\-–]\s*" + _PLACE + r"\*?\s*$", re.I | re.M),
    re.compile(r"\bremote\s*\(([^)\n]{2,40})\)", re.I),                          # "Remote (US)"
    re.compile(r"\bremote\s+(?:in|within|from)\s+(?:the\s+)?([A-Z][A-Za-z .&-]{1,40}?)(?=[.,;)\n]|\s+(?:only|and|or)\b|$)"),
    re.compile(r"\b(?:must|should|need to)\s+(?:be\s+)?(?:based|located|resid\w*|living|live)\s+in\s+(?:the\s+)?"
               r"([A-Za-z .&-]{2,40}?)(?=[.,;)\n]|\s+(?:only|and|or)\b|$)", re.I),
    re.compile(r"\b(?:authori[sz]ed|eligible|legally (?:able|permitted))\s+to\s+work\s+in\s+(?:the\s+)?"
               r"([A-Za-z .&-]{2,40}?)(?=[.,;)\n]|\s+(?:only|and|or|without)\b|$)", re.I),
]


def text_location_phrases(text: str) -> list[str]:
    """Places named by location phrases in the posting text (verbatim captures)."""
    out: list[str] = []
    for rx in TEXT_LOCATION_PATTERNS:
        for m in rx.finditer(text or ""):
            p = m.group(1).strip(" .,*")
            if re.search(r"(?i)\b(country|countries|jurisdiction|location|locations|region|state|city|area|"
                         r"office|this role|the role|which)\b", p) and not re.search(r"(?i)remote", p):
                continue  # "eligible to work in the country", "based in the location": names no place
            if p and p not in out:
                out.append(p)
    return out


# Time-zone / region working-hours limits ("EST or Europe time zones", "must work US hours", "PST overlap").
# Abbreviations are case-sensitive and need a time word nearby; IST / UTC+5:30 / India never restrict.
_TZ_ABBR = r"(?:EST|EDT|PST|PDT|CST|CDT|MST|MDT|ET|PT|CET|CEST|BST|AEST|AEDT|NZST)"
TIMEZONE_LIMITS = [
    re.compile(r"\b" + _TZ_ABBR + r"\b(?:\s*(?:/|or|and|,)\s*" + _TZ_ABBR + r"\b)*[^.\n]{0,30}?"
               r"\b(?:[Tt]ime\s?[Zz]ones?|[Hh]ours|[Oo]verlap|[Ww]orking|[Bb]usiness)"),
    re.compile(r"\b(?:[Tt]ime\s?[Zz]ones?|[Hh]ours|[Oo]verlap\w*|[Ww]ork(?:ing)?|[Bb]ased)\b[^.\n]{0,30}?\b" + _TZ_ABBR + r"\b"),
    re.compile(r"(?i)\b(?:us|u\.s\.|north american?|american|european|europe|emea|americas|latam|eastern|pacific|"
               r"central european|uk)\s+(?:time\s?zones?|working hours|business hours|hours)\b"),
    re.compile(r"(?i)\btime\s?zones?\s+(?:in|of|within|across)\s+(?:the\s+)?(?:us|u\.s\.|europe|emea|americas|"
               r"north america|latam|uk)\b"),
]
_INDIA_TZ = re.compile(r"(?i)\bIST\b|utc\s*\+\s*5[:.]?30|\bgmt\s*\+\s*5[:.]?30|\bindia\b")


def timezone_limit(text: str) -> str | None:
    """A phrase limiting a remote role to non-India time zones or regions, or None.

    None when the posting also names India or an Indian time zone anywhere (e.g. "IST or EST overlap")."""
    t = text or ""
    if _INDIA_TZ.search(t):
        return None
    for rx in TIMEZONE_LIMITS:
        m = rx.search(t)
        if m:
            return m.group(0).strip()[:80]
    return None


def text_location_check(text: str, cfg: IngestCfg) -> tuple[bool, str] | None:
    """For an 'open remote' board listing: does the posting text limit it to other countries?

    Returns None if the text names no place; (True, reason) if a phrase names India or open remote;
    (False, reason) if every named place is outside India (e.g. "Location: Remote (US)").
    """
    phrases = text_location_phrases(text)
    if not phrases:
        return None
    # Positive identification only: a phrase restricts the job if it names a known non-India country or
    # region (ingest.remote_exclude: US, Canada, UK, Europe, EMEA, ...). Sentences like "This is a globally
    # remote role" or "roles in every time zone" name no place and do not restrict anything.
    india = [p for p in phrases if any(_has_word(p.lower(), k) for k in cfg.india_keywords)]
    if india:
        return True, f"posting names an India location: {india[0][:40]}"
    limited = [p for p in phrases if any(_has_word(p.lower(), k) for k in cfg.remote_exclude)]
    if limited:
        return False, f"posting limits location: {'; '.join(limited)[:80]}"
    return True, "posting names no non-India place"
