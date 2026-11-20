"""Grounding check for agent answers (run in code after every turn).

Every job id and every fact number (pay, years, CGPA, dates, other amounts) in the answer must appear in the
tool outputs of that turn. Anything that does not is flagged. Jobs from the tool outputs that the answer names
by title must also carry their job id. Small bare integers without a unit (list numbering, "top 5") are skipped
and counted, so the report says what was not checked.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

ID_RX = re.compile(r"(?:job[ _-]?id|job|id)\s*[:#=]?\s*#?\s*(\d{1,7})\b|#(\d{1,7})\b|\[(\d{1,7})\]", re.I)
DATE_RX = re.compile(r"\b(20\d\d-\d\d-\d\d)\b")
NUM_RX = re.compile(
    r"(?<![\w.])(?P<cur>₹|rs\.?\s?|inr\s?|\$|usd\s?|€|£)?(?P<num>\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?P<suf>\s?(?:k\b|lpa\b|lakhs?\b|l\b|cr\b|crores?\b|%))?", re.I)
PAY_CTX = re.compile(r"₹|\brs\b|inr|\$|usd|stipend|salary|pay|ctc|lpa|lakh|per month|/month|/mo\b|monthly|annum|"
                     r"/yr|per year|compensation", re.I)
YEARS_CTX = re.compile(r"\byears?\b|\byrs?\b|experience", re.I)
CGPA_CTX = re.compile(r"cgpa|gpa|/\s?10\b|percent|%", re.I)
MULT = {"k": 1e3, "lpa": 1e5, "lakh": 1e5, "lakhs": 1e5, "l": 1e5, "cr": 1e7, "crore": 1e7, "crores": 1e7}


@dataclass
class Grounding:
    ok: bool = True
    job_ids_checked: list[int] = field(default_factory=list)
    numbers_checked: list[str] = field(default_factory=list)
    skipped_small_numbers: int = 0
    unsupported_job_ids: list[int] = field(default_factory=list)
    unsupported_numbers: list[dict[str, Any]] = field(default_factory=list)
    jobs_named_without_id: list[dict[str, Any]] = field(default_factory=list)
    verdict_missing: list[int] = field(default_factory=list)  # job listed without the verdict its tool returned
    verdict_mismatch: list[dict[str, Any]] = field(default_factory=list)  # answer states a different verdict
    rules_not_in_job_output: list[dict[str, Any]] = field(default_factory=list)  # rule named for a job, not in its lists
    rules_for_unchecked_job: list[dict[str, Any]] = field(default_factory=list)  # rules given, job's rules never fetched
    wrong_job_counts: list[dict[str, Any]] = field(default_factory=list)  # "checked N jobs" not matching any tool count
    # "pay / deadline not listed" said for a job whose field is not null, or was never in this turn's tool outputs
    unsupported_not_stated: list[dict[str, Any]] = field(default_factory=list)
    accepted_from_profile: list[str] = field(default_factory=list)  # equal to the user's real profile value
    accepted_from_user_message: list[str] = field(default_factory=list)  # the user typed this number this turn
    ok_tools_only: bool = True  # strict rule: profile / user-message values count as unsupported

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _walk(v: Any, nums: set[float], ids: set[int], texts: list[str], key: str = "") -> None:
    if isinstance(v, dict):
        for k, x in v.items():
            _walk(x, nums, ids, texts, k)
    elif isinstance(v, list):
        for x in v:
            _walk(x, nums, ids, texts, key)
    elif isinstance(v, bool) or v is None:
        return
    elif isinstance(v, int | float):
        nums.add(float(v))
        if key in ("job_id", "job_ids"):
            ids.add(int(v))
    elif isinstance(v, str):
        texts.append(v)
        for m in re.finditer(r"\d+(?:\.\d+)?", v.replace(",", "")):
            nums.add(float(m.group(0)))


def tool_facts(outputs: list[dict[str, Any]]) -> tuple[set[float], set[int], str, list[dict[str, Any]]]:
    """(numbers, job ids, joined text, job rows with id+title) from a turn's tool outputs."""
    nums: set[float] = set()
    ids: set[int] = set()
    texts: list[str] = []
    jobs: dict[int, dict[str, Any]] = {}
    for o in outputs:
        _walk(o, nums, ids, texts)
        stack: list[Any] = [o]
        while stack:
            x = stack.pop()
            if isinstance(x, dict):
                if isinstance(x.get("job_id"), int) and x.get("title"):
                    jobs[x["job_id"]] = {"job_id": x["job_id"], "title": x["title"]}
                stack.extend(x.values())
            elif isinstance(x, list):
                stack.extend(x)
    return nums, ids, "\n".join(texts) + "\n" + json.dumps(outputs, default=str), list(jobs.values())


def _supported(v: float, mult: float, nums: set[float], pay: bool) -> bool:
    cands = {v * mult} if mult != 1 else {v}
    if pay and mult == 1:
        cands |= {v * 1e3, v * 1e5}  # "25" in "₹25k/month" style splits; harmless widening for pay only
    if pay:  # the model may round pay ("about 27.5k"); 2% relative
        return any(abs(c - t) <= 0.02 * max(1.0, abs(t)) for c in cands for t in nums)
    return any(abs(c - t) <= 0.005 for c in cands for t in nums)


VERDICTS = ("ELIGIBLE", "CHECK", "INELIGIBLE")
VERDICT_RX = re.compile(r"\b(INELIGIBLE|ELIGIBLE)\b|\bCHECK\b", re.I)


def tool_verdicts(outputs: list[dict[str, Any]]) -> dict[int, str]:
    """job_id -> eligibility verdict as returned by any tool this turn."""
    out: dict[int, str] = {}
    stack: list[Any] = list(outputs)
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            v = x.get("eligibility") if x.get("eligibility") in VERDICTS else x.get("verdict")
            if isinstance(x.get("job_id"), int) and v in VERDICTS:
                out[x["job_id"]] = v
            stack.extend(x.values())
        elif isinstance(x, list):
            stack.extend(x)
    return out


RULE_WORDS = {
    "degree": r"degrees?",
    "specialization": r"speciali[sz]ations?|branch(?:es)?|disciplines?",
    "graduation_year": r"graduation[ _]year|batch(?:[ _]years?)?|grad(?:uation)? year",
    "cgpa": r"c?gpa",
    "experience": r"experience",
    "location": r"locations?",
    "pay": r"pay|stipend|salary",
    "employment_type": r"employment[ _]type|job type",
}
RULE_CTX = re.compile(r"(?i)needs?[ _]checking|need(?:s)? (?:to be )?(?:checked|verif\w*)|check(?:ing)? needed|to check|"
                      r"failed|fails?\b|failing|not met|does not meet|verify|verification|unknown")
_RULE_ANY = re.compile(r"(?i)\b(" + "|".join(f"(?P<{k}>{v})" for k, v in RULE_WORDS.items()) + r")\b")
COUNT_RX = re.compile(r"(?i)\b(?:checked|jobs? checked|postings? checked)\s*:?\s*\**\s*(\d+)\b|"
                      r"\b(\d+)\s+(?:jobs?|postings?|listings?|roles?)\s+(?:checked|returned|found|listed|compared)\b|"
                      r"\bchecked\s+(\d+)\s+(?:jobs?|postings?)")


def tool_rules(outputs: list[dict[str, Any]]) -> dict[int, set[str]]:
    """job_id -> rule names in that job's needs_checking / failed lists (only jobs whose rules were returned)."""
    out: dict[int, set[str]] = {}
    stack: list[Any] = list(outputs)
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            if isinstance(x.get("job_id"), int) and (isinstance(x.get("needs_checking"), list)
                                                      or isinstance(x.get("failed"), list)):
                s = out.setdefault(x["job_id"], set())
                s |= set(x.get("needs_checking") or []) | set(x.get("failed") or [])
                s |= {r["rule"] for r in x.get("rules") or [] if r.get("status") != "PASS"}
            stack.extend(x.values())
        elif isinstance(x, list):
            stack.extend(x)
    return out


def tool_counts(outputs: list[dict[str, Any]]) -> set[int]:
    """Job counts a statement like "checked N jobs" may truthfully refer to."""
    counts: set[int] = set()
    ids: set[int] = set()
    elig_calls = 0
    for o in outputs:
        for k in ("jobs_returned", "jobs_compared"):
            if isinstance(o.get(k), int):
                counts.add(o[k])
        if "verdict" in o and isinstance(o.get("job_id"), int):
            elig_calls += 1
    _walk_ids(outputs, ids)
    counts |= {len(ids), elig_calls}
    if sum(1 for o in outputs if isinstance(o.get("jobs_returned"), int)) > 1:
        counts.add(sum(o["jobs_returned"] for o in outputs if isinstance(o.get("jobs_returned"), int)))
    return counts


def _walk_ids(v: Any, ids: set[int]) -> None:
    if isinstance(v, dict):
        if isinstance(v.get("job_id"), int):
            ids.add(v["job_id"])
        for x in v.values():
            _walk_ids(x, ids)
    elif isinstance(v, list):
        for x in v:
            _walk_ids(x, ids)


_CLAUSE_END = re.compile(r"\]|\s[—–]\s|\.\s|\.$|;|\|")


def _rules_named(seg: str) -> set[str]:
    """Rule names written as eligibility claims in a job's answer segment (after "needs checking", "failed", ...),
    up to the end of that clause (so "needs checking: cgpa — no pay listed" names cgpa only)."""
    found: set[str] = set()
    for line in seg.splitlines():
        m = RULE_CTX.search(line)
        if not m:
            continue
        rest = line[m.start():]
        end = _CLAUSE_END.search(rest, len(m.group(0)))
        for r in _RULE_ANY.finditer(rest[:end.start()] if end else rest):
            found |= {k for k in RULE_WORDS if r.group(k)}
    return found


FIELD_WORDS = {"pay": r"pay|salary|stipend|ctc|compensation", "apply_deadline": r"deadline|last date|apply by"}
_NEG = (r"not (?:listed|stated|mentioned|given|provided|specified|disclosed|available|shown)|"
        r"does(?:n't| not) (?:state|list|mention|give|say|show)|none (?:of [\w ]+ )?(?:list|state|mention|give)s?")
_NO_FORM = r"\bno\b[^.\n]{{0,30}}?\b(?:{w})\b[^.\n]{{0,20}}?\b(?:listed|stated|given|mentioned|provided|shown)\b"
NOT_STATED = {k: re.compile(r"(?i)\b(?:" + w + r")\b[^.\n]{0,40}?(?:" + _NEG + r")|(?:" + _NEG
                            + r")[^.\n]{0,40}?\b(?:" + w + r")\b|" + _NO_FORM.format(w=w))
              for k, w in FIELD_WORDS.items()}


def tool_fields(outputs: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """job_id -> {field: value} for pay / apply_deadline, for every job whose output carried that field."""
    out: dict[int, dict[str, Any]] = {}
    stack: list[Any] = list(outputs)
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            if isinstance(x.get("job_id"), int):
                for k in FIELD_WORDS:
                    if k in x:
                        d = out.setdefault(x["job_id"], {})
                        d[k] = d.get(k) if d.get(k) is not None else x[k]
            stack.extend(x.values())
        elif isinstance(x, list):
            stack.extend(x)
    return out


def _not_stated_claims(text: str) -> set[str]:
    return {k for k, rx in NOT_STATED.items() if rx.search(text)}


def user_numbers(text: str) -> set[float]:
    out: set[float] = set()
    for m in NUM_RX.finditer(text or ""):
        v = float(m.group("num").replace(",", ""))
        out |= {v, v * MULT.get((m.group("suf") or "").strip().lower(), 1)}
    return out


def _verdict_found(seg: str) -> set[str]:
    found = set()
    for m in VERDICT_RX.finditer(seg):
        w = m.group(0)
        if w.upper() == "CHECK" and w != "CHECK":
            continue  # lowercase "check" is a verb, not the verdict
        found.add(w.upper())
    return found


def check_answer(answer: str, outputs: list[dict[str, Any]], profile_numbers: set[float] | None = None,
                 user_text: str = "") -> Grounding:
    g = Grounding()
    nums, ids, text, jobs = tool_facts(outputs)
    unums = user_numbers(user_text)
    ans = answer or ""
    stripped = re.sub(r"https?://\S+|\]\([^)]*\)", lambda m: " " * len(m.group(0)), ans)  # links are not facts
    id_spans: list[tuple[int, int]] = []
    for m in ID_RX.finditer(stripped):
        jid = int(next(x for x in m.groups() if x))
        id_spans.append(m.span())
        g.job_ids_checked.append(jid)
        if jid not in ids:
            g.unsupported_job_ids.append(jid)
    date_spans = []
    for m in DATE_RX.finditer(stripped):
        date_spans.append(m.span())
        g.numbers_checked.append(m.group(1))
        if m.group(1) not in text:
            g.unsupported_numbers.append({"value": m.group(1), "kind": "date", "context": ans[max(0, m.start() - 30):m.end() + 10]})
    for m in NUM_RX.finditer(stripped):
        s, e = m.span()
        if any(a <= s < b for a, b in id_spans + date_spans):
            continue
        line_start = stripped.rfind("\n", 0, s) + 1
        if re.fullmatch(r"\s*", stripped[line_start:s]) and re.match(r"[.)]", stripped[e:e + 1]):
            continue  # list numbering "1." / "2)"
        raw = m.group(0).strip()
        v = float(m.group("num").replace(",", ""))
        suf = (m.group("suf") or "").strip().lower()
        ctx = stripped[max(0, s - 40):e + 30]
        if m.group("cur") or suf in MULT:
            kind = "pay"
        elif YEARS_CTX.search(stripped[e:e + 15]):
            kind = "years"
        elif CGPA_CTX.search(stripped[max(0, s - 12):s]) or re.match(r"\s?(/\s?10\b|%)", stripped[e:e + 6]):
            kind = "cgpa"
        elif PAY_CTX.search(ctx):
            kind = "pay"
        else:
            kind = "other"
        if kind == "other" and v == int(v) and v <= 31 and "." not in m.group("num"):
            g.skipped_small_numbers += 1
            continue
        g.numbers_checked.append(raw)
        if suf == "%" and kind == "cgpa":
            ok = _supported(v, 1, nums, False)
        else:
            ok = _supported(v, MULT.get(suf, 1), nums, kind == "pay")
        if not ok:
            flag = {"value": raw, "kind": kind, "context": ans[max(0, s - 30):e + 15].strip()}
            if any(abs(v - p) < 1e-6 for p in profile_numbers or ()):
                g.accepted_from_profile.append(raw)
            elif _supported(v, MULT.get(suf, 1), unums, kind == "pay"):
                g.accepted_from_user_message.append(raw)
            else:
                g.unsupported_numbers.append(flag)
    low = ans.lower()
    mentioned = set(g.job_ids_checked)
    for j in jobs:
        t = j["title"].lower()
        if len(t) >= 8 and t in low and j["job_id"] not in mentioned:
            g.jobs_named_without_id.append(j)
    # Every listed job must show the verdict its tool returned (and not a different one). The segment for a job
    # runs from the start of the line holding its id to the start of the line holding the next id.
    verdicts = tool_verdicts(outputs)
    rules_by_job = tool_rules(outputs)
    fields = tool_fields(outputs)
    occ = sorted((sp[0], jid) for sp, jid in zip(id_spans, [int(next(x for x in m.groups() if x))
                                                            for m in ID_RX.finditer(stripped)], strict=True))
    starts = [stripped.rfind("\n", 0, pos) + 1 for pos, _ in occ]
    for i, (_, jid) in enumerate(occ):
        if i and occ[i - 1][1] == jid:
            continue
        if jid not in verdicts:
            named = _rules_named(stripped[starts[i]:starts[i + 1] if i + 1 < len(occ) and starts[i + 1] > starts[i]
                                          else len(stripped)])
            if named and jid not in rules_by_job:
                g.rules_for_unchecked_job.append({"job_id": jid, "rules": sorted(named)})
            continue
        seg = stripped[starts[i]:starts[i + 1] if i + 1 < len(occ) and starts[i + 1] > starts[i] else len(stripped)]
        named = _rules_named(seg)
        if named:
            if jid not in rules_by_job:
                g.rules_for_unchecked_job.append({"job_id": jid, "rules": sorted(named)})
            elif named - rules_by_job[jid]:
                g.rules_not_in_job_output.append({"job_id": jid, "rules": sorted(named - rules_by_job[jid]),
                                                  "tool": sorted(rules_by_job[jid])})
        for fld in _not_stated_claims(seg):
            _check_not_stated(g, fields, jid, fld, seg)
        found = _verdict_found(seg)
        if not found:
            g.verdict_missing.append(jid)
        elif verdicts[jid] not in found:
            g.verdict_mismatch.append({"job_id": jid, "tool": verdicts[jid], "answer": sorted(found)})
    # Lines naming no job ("none of these list a salary") speak for every job in the answer.
    for line in stripped.splitlines():
        if ID_RX.search(line):
            continue
        for fld in _not_stated_claims(line):
            for jid in dict.fromkeys(g.job_ids_checked):
                _check_not_stated(g, fields, jid, fld, line)
    counts = tool_counts(outputs)
    for m in COUNT_RX.finditer(stripped):
        n = int(next(x for x in m.groups() if x))
        if outputs and n not in counts:
            g.wrong_job_counts.append({"stated": n, "tool_counts": sorted(counts), "context": m.group(0)})
    base = not (g.unsupported_job_ids or g.unsupported_numbers or g.jobs_named_without_id or g.verdict_missing
                or g.verdict_mismatch or g.rules_not_in_job_output or g.rules_for_unchecked_job
                or g.wrong_job_counts or g.unsupported_not_stated)
    g.ok = base
    g.ok_tools_only = base and not (g.accepted_from_profile or g.accepted_from_user_message)
    return g


def _check_not_stated(g: Grounding, fields: dict[int, dict[str, Any]], jid: int, fld: str, ctx: str) -> None:
    have = fields.get(jid, {})
    if fld not in have:
        why = "field not in this turn's tool outputs"
    elif have[fld] is not None:
        why = f"tool has {have[fld]}"
    else:
        return
    item = {"job_id": jid, "field": fld, "why": why, "context": ctx.strip()[:120]}
    if not any(x["job_id"] == jid and x["field"] == fld for x in g.unsupported_not_stated):
        g.unsupported_not_stated.append(item)
