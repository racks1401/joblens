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
    only_profile_values_flagged: bool = False  # every flag is the user's own profile value (still a FAIL)

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


def check_answer(answer: str, outputs: list[dict[str, Any]], profile_numbers: set[float] | None = None) -> Grounding:
    g = Grounding()
    nums, ids, text, jobs = tool_facts(outputs)
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
            g.unsupported_numbers.append({"value": raw, "kind": kind, "context": ans[max(0, s - 30):e + 15].strip(),
                                          "in_profile": any(abs(v - p) < 1e-6 for p in profile_numbers or ())})
    low = ans.lower()
    mentioned = set(g.job_ids_checked)
    for j in jobs:
        t = j["title"].lower()
        if len(t) >= 8 and t in low and j["job_id"] not in mentioned:
            g.jobs_named_without_id.append(j)
    g.ok = not (g.unsupported_job_ids or g.unsupported_numbers or g.jobs_named_without_id)
    g.only_profile_values_flagged = (not g.ok and not g.unsupported_job_ids and not g.jobs_named_without_id
                                     and all(u.get("in_profile") for u in g.unsupported_numbers))
    return g
