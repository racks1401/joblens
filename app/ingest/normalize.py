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
