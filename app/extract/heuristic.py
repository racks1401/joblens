"""Rule-based JobFacts / Profile extractor used by the offline fake LLM provider.

It is a transparent baseline, not an LLM: regexes over the posting text. Every value it returns
carries an evidence span copied verbatim from the text, so it passes the same evidence check as a
real model. Model name in stored rows: "fake-heuristic".
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any

from app.extract.vocab import (
    CITY_ALIASES,
    CONTEXT_REQUIRED,
    DEGREE_PATTERNS,
    SKILL_RES,
    SPECIALIZATION_PATTERNS,
    norm_location,
)
from app.llm.client import LLMResult

Ev = dict[str, Any]
NULL: Ev = {"value": None, "evidence": None}


def snippet(text: str, start: int, end: int, width: int = 70) -> str:
    """A verbatim span (<=200 chars) of the line around [start, end)."""
    ls = text.rfind("\n", 0, start) + 1
    le = text.find("\n", end)
    le = len(text) if le == -1 else le
    a, b = max(ls, start - width), min(le, end + width)
    if b - a > 200:
        a, b = max(ls, start - 20), min(le, start - 20 + 200)
        if b < end:
            a, b = start, min(le, start + 200)
    return text[a:b].strip()


def _first(text: str, pattern: str, flags: int = re.I) -> re.Match[str] | None:
    return re.search(pattern, text, flags)


def _ev(value: Any, text: str, m: re.Match[str]) -> Ev:
    return {"value": value, "evidence": snippet(text, m.start(), m.end())}


def _header(text: str, name: str) -> re.Match[str] | None:
    return re.search(rf"^{name}:[ \t]*(.+)$", text, re.M)


def employment_type(text: str) -> Ev:
    t = _header(text, "Title")
    if t and re.search(r"\bintern(ship)?\b", t.group(1), re.I):
        return _ev("internship", text, t)
    for value, pat in [
        ("internship", r"\b(?:employment|job) type\s*[:\-]\s*intern"),
        ("full_time", r"\b(?:employment|job) type\s*[:\-]\s*full"),
        ("part_time", r"\bpart[- ]time\b"),
        ("contract", r"\bcontractual\b|\bcontract (?:role|position|basis|job|employee)\b|\bfreelance\b"),
        ("full_time", r"\bfull[- ]time\b|\bpermanent (?:role|position)\b"),
        ("internship", r"\binternship\b"),
    ]:
        m = _first(text, pat)
        if m:
            return _ev(value, text, m)
    return dict(NULL)


def work_mode(text: str) -> Ev:
    loc = _header(text, "Location")
    if loc and re.search(r"\bremote\b", loc.group(1), re.I):
        return _ev("remote", text, loc)
    for value, pat in [
        ("hybrid", r"\bhybrid\b"),
        ("remote", r"\b(?:fully |100% )?remote\b(?! work experience)|\bwork from home\b|\bWFH\b"),
        ("onsite", r"\bon[- ]?site\b|\bin[- ]office\b|\bwork from office\b|\bWFO\b"),
    ]:
        m = _first(text, pat)
        if m:
            return _ev(value, text, m)
    return dict(NULL)


def locations(text: str) -> Ev:
    loc = _header(text, "Location")
    if loc:
        vals: list[str] = []
        for part in re.split(r"\s*[/;|]\s*", loc.group(1)):
            if not part or re.fullmatch(r"(?i)remote.*|anywhere|worldwide", part.strip()):
                continue
            canon = norm_location(part)
            if canon and canon not in vals:
                vals.append(canon)
        if len(vals) > 1 and "India" in vals:
            vals.remove("India")
        if vals:
            return {"value": vals, "evidence": loc.group(0).strip()[:200]}
    city_re = r"\b(" + "|".join(re.escape(c) for c in CITY_ALIASES) + r")\b"
    found = [(m, CITY_ALIASES[m.group(1).lower()]) for m in re.finditer(city_re, text, re.I)]
    if found:
        vals = list(dict.fromkeys(c for _, c in found))
        return _ev(vals[:5], text, found[0][0])
    return dict(NULL)


def duration_months(text: str) -> Ev:
    for m in re.finditer(r"\b(\d{1,2})(?:\s*(?:-|–|to)\s*(\d{1,2}))?\s*months?\b", text, re.I):
        line = text[text.rfind("\n", 0, m.start()) + 1: text.find("\n", m.end()) if "\n" in text[m.end():] else None]
        if re.search(r"duration|internship|period|tenure|program|contract", line, re.I):
            val = float(m.group(2) or m.group(1))
            if 0 < val <= 24:
                return _ev(val, text, m)
    return dict(NULL)


_NUM = r"(\d[\d,]*(?:\.\d+)?)"
_UNIT = r"\s*(k|K|lakhs?|lacs?|L|LPA|lpa)?"
_PERIOD = r"(?:\s*(?:/|per|a|an|p\.?)\s*-?\s*(month|mo|year|annum|yr|hour|hr|a)\b|\s*(LPA|lpa|p\.?a\.?)\b)?"


def _num(n: str, unit: str | None) -> float:
    v = float(n.replace(",", ""))
    u = (unit or "").lower()
    if u == "k":
        v *= 1000
    elif u.startswith(("lakh", "lac", "l")):
        v *= 100000
    return v


def _period(p: str | None, unit: str | None) -> str | None:
    if unit and unit.lower() == "lpa":
        return "year"
    if not p:
        return None
    p = p.lower().replace(".", "")
    return {"month": "month", "mo": "month", "year": "year", "annum": "year", "yr": "year", "hour": "hour",
            "hr": "hour", "lpa": "year", "pa": "year"}.get(p)


def pay(text: str) -> Ev:
    pats = [
        ("INR", rf"(?:₹|\bRs\.?|\bINR)\s*{_NUM}{_UNIT}(?:\s*(?:-|–|to)\s*(?:₹|Rs\.?|INR)?\s*{_NUM}{_UNIT})?{_PERIOD}"),
        ("USD", rf"(?:\$|\bUSD\s?){_NUM}{_UNIT}(?:\s*(?:-|–|to)\s*(?:\$|USD\s?)?{_NUM}{_UNIT})?{_PERIOD}"),
        ("INR", rf"\b{_NUM}\s*(?:-|–|to)?\s*{_NUM}?\s*(LPA|lpa)\b"),
        (None, rf"\bstipend\b[^\n\d]{{0,25}}{_NUM}{_UNIT}(?:\s*(?:-|–|to)\s*{_NUM}{_UNIT})?{_PERIOD}"),
    ]
    for cur, pat in pats:
        m = re.search(pat, text)
        if not m:
            continue
        g = m.groups()
        if cur == "INR" and pat.endswith(r"(LPA|lpa)\b"):
            lo = _num(g[0], "l")
            hi = _num(g[1], "l") if g[1] else lo
            money: dict[str, Any] = {"min": lo, "max": hi, "currency": "INR", "period": "year"}
        else:
            lo = _num(g[0], g[1])
            hi = _num(g[2], g[3] or g[1]) if g[2] else lo
            if g[1] and not g[3] and g[2]:
                lo = _num(g[0], g[1])
            per = _period(g[4], g[1]) or _period(g[5], g[3]) or _period(g[5], None)
            if g[3] and g[3].lower() == "lpa":
                per = "year"
            money = {"min": lo, "max": hi, "currency": cur, "period": per}
        if money["min"] and money["min"] >= 100:
            return _ev(money, text, m)
    return dict(NULL)


def degrees_allowed(text: str) -> Ev:
    found: list[tuple[int, str, re.Match[str]]] = []
    for code, pat, flags in DEGREE_PATTERNS:
        m = re.search(pat, text, flags)
        if m:
            found.append((m.start(), code, m))
    if not found:
        return dict(NULL)
    found.sort()
    codes = list(dict.fromkeys(c for _, c, _ in found))
    if "ANY_GRADUATE" in codes:
        codes = ["ANY_GRADUATE"]
    return _ev(codes, text, found[0][2])


def specializations(text: str) -> Ev:
    lines = [ln for ln in text.split("\n") if re.search(r"degree|branch|stream|graduat|B\.?\s?Tech|B\.?E\b|bachelor|master|MCA|BCA|B\.?Sc|discipline|major", ln, re.I)]
    scope = "\n".join(lines)
    found: list[tuple[int, str]] = []
    for name, pat, flags in SPECIALIZATION_PATTERNS:
        m = re.search(pat, scope, flags)
        if m:
            found.append((m.start(), name))
    if not found:
        return dict(NULL)
    found.sort()
    names = ["all"] if any(n == "all" for _, n in found) else list(dict.fromkeys(n for _, n in found))
    first = next(p for n, p, f in SPECIALIZATION_PATTERNS if n == (names[0] if names[0] != "all" else "all"))
    flags = next(f for n, p, f in SPECIALIZATION_PATTERNS if p == first)
    hit = re.search(first, text, flags)
    return _ev(names, text, hit) if hit else dict(NULL)


def graduation_years(text: str) -> Ev:
    years: list[int] = []
    start: int | None = None
    for ln_m in re.finditer(r"^.*$", text, re.M):
        ln = ln_m.group(0)
        if not re.search(r"batch|graduat|passing|pass[- ]?out|class of|year of|graduating|grads?\b", ln, re.I):
            continue
        for ym in re.finditer(r"\b(20[2-3]\d)\b", ln):
            y = int(ym.group(1))
            if y not in years:
                years.append(y)
            if start is None:
                start = ln_m.start() + ym.start()
    if not years or start is None:
        return dict(NULL)
    return {"value": sorted(years), "evidence": snippet(text, start, start + 4)}


def min_cgpa(text: str) -> Ev:
    pats = [
        r"\b(\d{1,2}(?:\.\d{1,2})?)\s*(?:/\s*10\s*)?\+?\s*(?:CGPA|CPI|GPA)\b",
        r"\b(?:CGPA|CPI|GPA)\s*(?:of\s*|:\s*|-\s*)?(?:at\s*least\s*|minimum\s*(?:of\s*)?|min\.?\s*|>=?\s*|above\s*|over\s*)?(\d{1,2}(?:\.\d{1,2})?)(?!\s*%)",
    ]
    for pat in pats:
        for m in re.finditer(pat, text, re.I):
            v = float(m.group(1))
            after = text[m.end(): m.end() + 6]
            if 4 < v <= 10 and "/4" not in after:
                return _ev(v, text, m)
    return dict(NULL)


def experience_min_years(text: str) -> Ev:
    m = _first(text, r"\b(?:no (?:prior )?experience (?:is )?required|freshers? (?:can|are welcome|may) apply|0\s*(?:-|–|to)\s*\d\s*(?:years?|yrs?))")
    if m:
        return _ev(0.0, text, m)
    for pat in [
        r"\b(\d{1,2})\s*\+?\s*(?:(?:-|–|to)\s*\d{1,2}\s*)?(?:years?|yrs?)(?:'|’)?\b[^.\n]{0,40}?\bexperience",
        r"\bexperience\b[^.\n]{0,30}?\b(\d{1,2})\s*\+?\s*(?:years?|yrs?)\b",
        r"\bminimum (?:of )?(\d{1,2}) years?\b",
    ]:
        m = _first(text, pat)
        if m:
            v = float(m.group(1))
            if v <= 30:
                return _ev(v, text, m)
    return dict(NULL)


def fresher_friendly(text: str) -> Ev:
    m = _first(text, r"\bfreshers?\b|\bno (?:prior )?experience (?:is )?(?:required|needed)\b|\bentry[- ]level\b|"
                     r"\bnew grad(?:uate)?s?\b|\brecent (?:college )?graduates?\b|\bfinal[- ]year students?\b|"
                     r"\bcurrently (?:pursuing|enrolled)\b|\b0\s*(?:-|–|to)\s*1 years?\b|\bearly[- ]career\b")
    return _ev(True, text, m) if m else dict(NULL)


_NICE_HEAD = re.compile(r"nice[- ]to[- ]have|preferred|bonus|good[- ]to[- ]have|\bplus\b|desirable|added advantage|"
                        r"brownie points|extra credit|would be great", re.I)


def _sections(text: str) -> tuple[str, str]:
    """Split text into (required-ish, nice-to-have) parts by headings."""
    req: list[str] = []
    nice: list[str] = []
    mode = "req"
    for ln in text.split("\n"):
        is_heading = not ln.startswith("- ") and len(ln) < 90
        if is_heading and _NICE_HEAD.search(ln):
            mode = "nice"
        elif is_heading and ln.endswith(":") or (is_heading and mode == "nice" and ln and not _NICE_HEAD.search(ln)
                                                  and len(ln.split()) <= 6):
            mode = "req"
        (nice if mode == "nice" or (ln.startswith("- ") and _NICE_HEAD.search(ln)) else req).append(ln)
    return "\n".join(req), "\n".join(nice)


def _skills_in(text: str, full: str) -> Ev:
    hits: list[tuple[int, str, re.Match[str]]] = []
    for skill, rx in SKILL_RES:
        m = rx.search(text)
        if not m:
            continue
        if skill in CONTEXT_REQUIRED:
            ctx = text[max(0, m.start() - 60): m.end() + 60]
            if not re.search(r"python|java|programming|language|golang|scala|c\+\+|kotlin|sql", ctx, re.I):
                continue
        hits.append((m.start(), skill, m))
    if not hits:
        return dict(NULL)
    hits.sort()
    skills = list(dict.fromkeys(s for _, s, _ in hits))[:25]
    m0 = hits[0][2]
    # evidence must be a substring of the full text; the section text is made of full-text lines
    line = text[text.rfind("\n", 0, m0.start()) + 1:]
    line = line.split("\n", 1)[0]
    pos = full.find(line)
    if pos < 0:
        return {"value": skills, "evidence": None}
    i = pos + (m0.start() - (text.rfind("\n", 0, m0.start()) + 1))
    return {"value": skills, "evidence": snippet(full, i, i + len(m0.group(0)))}


def skills(text: str) -> tuple[Ev, Ev]:
    req, nice = _sections(text)
    r, n = _skills_in(req, text), _skills_in(nice, text)
    if r["value"] and n["value"]:
        n["value"] = [s for s in n["value"] if s not in r["value"]] or None
        if n["value"] is None:
            n = dict(NULL)
    return r, n


_MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"


def _parse_date(s: str) -> date | None:
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", s.strip().replace(",", ""))
    for fmt in ("%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y"):
        try:
            return datetime.strptime(s.replace("Sept", "Sep"), fmt).date()
        except ValueError:
            continue
    return None


def apply_deadline(text: str) -> Ev:
    pat = (r"(?:apply by|last date(?: to apply| of application| for applications)?|deadline(?: to apply)?|"
           r"applications? close[sd]?(?: on)?|apply before)\s*(?:is|:|-|on)?\s*"
           rf"(\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTHS})[a-z]*,?\s+\d{{4}}|(?:{_MONTHS})[a-z]*\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}|"
           r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})")
    m = re.search(pat, text, re.I)
    if m:
        d = _parse_date(m.group(1))
        if d:
            return _ev(d.isoformat(), text, m)
    return dict(NULL)


def ppo(text: str) -> Ev:
    m = _first(text, r"\bPPO\b|pre[- ]placement offer|conver(?:t|sion)(?:ed)? (?:in)?to (?:a )?full[- ]time|"
                     r"full[- ]time offer (?:based on|upon|after)")
    return _ev(True, text, m) if m else dict(NULL)


RED_FLAG_PATTERNS: list[tuple[str, str]] = [
    ("asks for a fee or deposit", r"\b(?:registration|security|training|joining|processing|application|kit|onboarding)\s+"
                                  r"(?:fee|fees|deposit|charges?|amount)\b|\brefundable (?:deposit|fee|amount)\b|"
                                  r"\bpay (?:a )?(?:small )?(?:fee|amount)\b|\bpay (?:rs\.?|₹|inr)\s*\d"),
    ("contact only via WhatsApp/Telegram",
     r"\b(?:contact|message|msg|text|reach|ping|apply|send|share|dm|join)\b[^.\n]{0,40}\b(?:whats\s?app|telegram)\b|"
     r"\b(?:whats\s?app|telegram)\b\s*(?:only|number|no\.?|group|:|-|at|on)\s*[+\d(]|"
     r"\b(?:whats\s?app|telegram)\b[^.\n]{0,20}\+?\d[\d\s-]{8,}"),
    ("promises guaranteed income", r"\bguaranteed (?:income|earnings?|salary|returns|job|placement)\b|"
                                   r"\bearn (?:up to )?(?:rs\.?|₹|inr)?\s*[\d,]+\s*k?\s*(?:per|/|a|every)\s*(?:day|week)\b|"
                                   r"\bwork from home and earn\b|\bno interview\b|\bdaily payout\b"),
]


_NEGATION = re.compile(r"\b(?:never|not|no one|nobody|don'?t|do not|won'?t|will not|doesn'?t|does not|without)\b"
                       r"[^.\n]{0,40}$|\bbeware\b|\bfraud(?:ulent|sters?)?\b|\bscam", re.I)


def first_unnegated(pat: str, text: str) -> re.Match[str] | None:
    """First match not preceded (same sentence, 60 chars) by a negation or a scam-warning word."""
    for m in re.finditer(pat, text, re.I):
        before = text[max(0, m.start() - 60): m.start()]
        before = re.split(r"[.!?\n]", before)[-1]
        if not _NEGATION.search(before):
            return m
    return None


def red_flags(text: str) -> list[Ev]:
    out = []
    for desc, pat in RED_FLAG_PATTERNS:
        m = first_unnegated(pat, text)
        if m:
            out.append(_ev(desc, text, m))
    return out


def extract_facts(text: str) -> dict[str, Any]:
    """Return a JobFacts-shaped dict for a posting text."""
    title = _header(text, "Title")
    company = _header(text, "Company")
    req, nice = skills(text)
    return {
        "title": title.group(1).strip() if title else (text.strip().split("\n", 1)[0][:120] or "Unknown"),
        "company": company.group(1).strip() if company and company.group(1).strip() else None,
        "employment_type": employment_type(text),
        "work_mode": work_mode(text),
        "locations": locations(text),
        "duration_months": duration_months(text),
        "pay": pay(text),
        "degrees_allowed": degrees_allowed(text),
        "specializations": specializations(text),
        "graduation_years": graduation_years(text),
        "min_cgpa": min_cgpa(text),
        "experience_min_years": experience_min_years(text),
        "fresher_friendly": fresher_friendly(text),
        "required_skills": req,
        "nice_to_have_skills": nice,
        "apply_deadline": apply_deadline(text),
        "ppo_or_conversion": ppo(text),
        "red_flags": red_flags(text),
    }


def _between_quotes(messages: list[dict[str, Any]]) -> str:
    content = str(messages[-1].get("content") or "")
    # the original prompt is the first user message; retries append more messages
    for m in messages:
        if m.get("role") == "user" and '"""' in str(m.get("content")):
            content = str(m["content"])
            break
    parts = content.split('"""')
    return parts[1].strip("\n") if len(parts) >= 3 else content


def fake_facts_handler(messages: list[dict[str, Any]], tools: Any) -> LLMResult:
    """Fake-LLM handler for facts_* prompts."""
    return LLMResult(content=json.dumps(extract_facts(_between_quotes(messages))))


def extract_profile(text: str) -> dict[str, Any]:
    """Very small resume parser (fake LLM only)."""
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), None)
    deg = degrees_allowed(text)["value"]
    deg_code = next((d for d in (deg or []) if d in {"MCA", "MTECH", "MSC", "MBA", "PHD"}), None) or (deg or [None])[0]
    years = [int(y) for y in re.findall(r"\b(20[1-3]\d)\b", text)]
    cg = re.search(r"\b(?:CGPA|CPI|GPA)\s*[:\-]?\s*(\d{1,2}(?:\.\d{1,2})?)|\b(\d\.\d{1,2})\s*/\s*10\b", text, re.I)
    sk = _skills_in(text, text)["value"] or []
    spec = re.search(r"\((?:[^()]*?)(AI\s*&\s*IoT|Computer Science|Information Technology|Data Science|Electronics|AI\s*&\s*ML)\)|"
                     r"\bin (Computer Science|Information Technology|Data Science|Electronics|Mechanical|Electrical)\b", text, re.I)
    exp = re.search(r"\b(\d{1,2}(?:\.\d)?)\+?\s*years? of (?:professional |work )?experience\b", text, re.I)
    return {
        "name": first if first and len(first) < 60 and not re.search(r"@|\d{5}", first) else None,
        "degree": deg_code,
        "specialization": (spec.group(1) or spec.group(2)) if spec else None,
        "graduation_year": max(years) if years else None,
        "cgpa": float(cg.group(1) or cg.group(2)) if cg else None,
        "experience_years": float(exp.group(1)) if exp else 0,
        "skills": [{"name": s, "level": None} for s in sk],
        "target_roles": [],
        "preferred_locations": locations(text)["value"] or [],
        "work_modes": [],
        "min_monthly_pay_inr": None,
        "employment_types": [],
        "strict_location": False,
    }


def fake_profile_handler(messages: list[dict[str, Any]], tools: Any) -> LLMResult:
    """Fake-LLM handler for profile_* prompts."""
    return LLMResult(content=json.dumps(extract_profile(_between_quotes(messages))))
