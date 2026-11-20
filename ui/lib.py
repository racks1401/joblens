"""Shared UI helpers: API client (with a per-browser-session id for rate limits), current profile, card pieces.

Every value shown on a card comes from an API response field. Model-written text (explanation summaries, agent
answers) is always labeled as such and never used for a card value.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import httpx
import streamlit as st

API_URL = os.environ.get("API_URL", "http://localhost:8000")

VERDICT = {  # word + icon + colour, so colour is never the only signal
    "ELIGIBLE": ("green", ":material/check_circle:", "ELIGIBLE"),
    "CHECK": ("orange", ":material/help:", "CHECK"),
    "INELIGIBLE": ("red", ":material/cancel:", "INELIGIBLE"),
}
TRUST = {"ok": ("green", "trust OK"), "caution": ("orange", "trust CAUTION"), "risky": ("red", "trust RISKY")}
STATUS_GROUP = {"PASS": "passed", "FAIL": "failed", "UNKNOWN": "needs checking", "SOFT_FAIL": "needs checking"}
DEGREES = ["BTECH", "BE", "BCA", "BSC", "BACHELORS", "MTECH", "MCA", "MSC", "MBA", "MASTERS", "PHD"]
WORK_MODES = ["onsite", "hybrid", "remote"]
EMPLOYMENT_TYPES = ["internship", "full_time", "part_time", "contract"]


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def client_session_id() -> str:
    if "client_session_id" not in st.session_state:
        st.session_state.client_session_id = uuid.uuid4().hex
    return str(st.session_state.client_session_id)


def api(method: str, path: str, timeout: float = 120, **kw: Any) -> Any:
    """Call the API; raise APIError with the server's message on 4xx/5xx."""
    headers = {"X-Session-Id": client_session_id(), **kw.pop("headers", {})}
    try:
        r = httpx.request(method, f"{API_URL}{path}", headers=headers, timeout=timeout, **kw)
    except httpx.HTTPError as e:
        raise APIError(0, "api_unreachable", f"The API is not reachable at {API_URL} ({type(e).__name__}).") from e
    if r.status_code >= 400:
        try:
            err = r.json().get("error", {})
        except ValueError:
            err = {}
        raise APIError(r.status_code, err.get("code", "error"), err.get("message", r.text[:300]))
    return r.json()


def show_error(e: APIError) -> None:
    if e.status == 429:
        st.warning(f"**Limit reached.** {e.message}", icon=":material/hourglass_empty:")
    elif e.status == 0:
        st.error(e.message, icon=":material/cloud_off:")
    else:
        st.error(f"{e.message} ({e.code}, HTTP {e.status})")


def current_profile() -> dict[str, Any]:
    """The profile this browser session uses: the user's saved one, else the synthetic demo profile."""
    pid = st.session_state.get("profile_id")
    if pid:
        try:
            return dict(api("GET", f"/profiles/{pid}"))
        except APIError:
            st.session_state.pop("profile_id", None)
    return dict(api("GET", "/profiles/demo"))


def is_demo(p: dict[str, Any]) -> bool:
    return str(p["id"]).endswith("d3e0")


def profile_banner(p: dict[str, Any]) -> None:
    prof = p["profile"]
    who = "synthetic demo profile" if is_demo(p) else "your saved profile"
    st.caption(f"Using the **{who}**: {prof['degree']} {prof.get('specialization') or ''}, batch "
               f"{prof['graduation_year']}, CGPA {prof.get('cgpa') if prof.get('cgpa') is not None else 'not set'}. "
               "Change it on the Profile page.")


def verdict_badge(v: str | None) -> str:
    if not v:
        return ":gray-background[eligibility not checked]"
    colour, icon, word = VERDICT[v]
    return f":{colour}-background[{icon} **{word}**]"


def trust_badge(level: str | None, score: int | None = None) -> str:
    if not level:
        return ":gray-background[trust not computed]"
    colour, word = TRUST[level]
    return f":{colour}-background[{word}{f' {score}/100' if score is not None else ''}]"


def money(pay: dict[str, Any] | None) -> str:
    """Pay exactly as stored, in its own currency and period; INR also shows the monthly figure the API computed."""
    if not pay:
        return "not stated"
    lo, hi, cur, per = pay.get("min"), pay.get("max"), pay.get("currency") or "?", pay.get("period") or "?"
    amt = f"{lo:,.0f}" if lo == hi or hi is None else (f"{lo:,.0f} – {hi:,.0f}" if lo is not None else f"up to {hi:,.0f}")
    text = f"{cur} {amt} per {per}"
    if pay.get("inr_per_month") and per != "month":
        text += f" (≈ ₹{pay['inr_per_month']:,} per month)"
    if pay.get("note"):
        text += " (not converted)"
    return text


def field(v: Any) -> str:
    return "not stated" if v in (None, "", []) else str(v).replace("_", " ")


def eligibility_lists(elig: dict[str, Any] | None) -> None:
    """The exact passed / failed / needs-checking rule lists from the eligibility engine."""
    if not elig:
        st.caption("Eligibility was not checked for this job.")
        return
    groups: dict[str, list[dict[str, Any]]] = {"failed": [], "needs checking": [], "passed": []}
    for r in elig["rules"]:
        groups[STATUS_GROUP[r["status"]]].append(r)
    cols = st.columns(3)
    for col, (name, icon) in zip(cols, (("failed", "✗"), ("needs checking", "?"), ("passed", "✓")), strict=True):
        with col:
            st.markdown(f"**{icon} {name.capitalize()} ({len(groups[name])})**")
            for r in groups[name]:
                st.markdown(f"- `{r['rule']}`: {r['message']}")
            if not groups[name]:
                st.caption("none")


def trust_reasons(trust: dict[str, Any] | None) -> None:
    if not trust:
        st.caption("Trust was not computed for this job.")
        return
    if not trust["reasons"]:
        st.caption("No trust deductions: no scam rule or ghost-job signal fired.")
    for r in trust["reasons"]:
        st.markdown(f"- `{r['code']}` ({r['delta']:+d}): {r['message']}")
    st.caption("Only rule checks mark scam signals; the text classifier is trained on old US/UK data and is not "
               "validated on Indian postings.")


FACT_LABELS = [
    ("employment_type", "Job type"), ("work_mode", "Work mode"), ("locations", "Locations"),
    ("duration_months", "Duration (months)"), ("pay", "Pay"), ("degrees_allowed", "Degrees allowed"),
    ("specializations", "Branches"), ("graduation_years", "Batch years"), ("min_cgpa", "Minimum CGPA"),
    ("experience_min_years", "Minimum experience (years)"), ("fresher_friendly", "Fresher friendly"),
    ("required_skills", "Required skills"), ("nice_to_have_skills", "Nice-to-have skills"),
    ("apply_deadline", "Apply deadline"), ("ppo_or_conversion", "PPO / conversion"),
]


def _fact_value(key: str, v: Any) -> str:
    if v is None:
        return "not stated"
    if key == "pay":
        return money(v)
    if isinstance(v, list):
        return ", ".join(str(x) for x in v) or "not stated"
    if isinstance(v, bool):
        return "yes" if v else "no"
    return str(v).replace("_", " ")


def facts_with_quotes(facts: dict[str, Any] | None) -> None:
    """Every extracted requirement next to the verbatim quote it came from (null = the posting does not state it)."""
    if not facts:
        st.caption("No extracted facts for this job yet.")
        return
    rows = []
    for key, label in FACT_LABELS:
        f = facts.get(key) or {}
        rows.append({"Requirement": label, "Value": _fact_value(key, f.get("value")),
                     "Evidence quote from the posting": f.get("evidence") or "—"})
    st.dataframe(rows, hide_index=True, use_container_width=True, height=36 * (len(rows) + 1) + 3,
                 column_config={"Evidence quote from the posting": st.column_config.TextColumn(width="large")})
    flags = [x for x in facts.get("red_flags") or [] if x.get("value")]
    if flags:
        st.markdown("**Red flags quoted from the posting**")
        for x in flags:
            st.markdown(f"- {x['value']}: “{x.get('evidence') or ''}”")


def job_card_header(j: dict[str, Any], elig: dict[str, Any] | None) -> None:
    facts = j.get("facts") or {}
    st.markdown(f"### {j['title']}")
    locs = (facts.get("locations") or {}).get("value") or []
    st.markdown(f"**{j['company'] or 'Company not stated'}** · "
                f"{j.get('location_raw') or ', '.join(locs) or 'location not stated'} · "
                f"{field((facts.get('work_mode') or {}).get('value'))}")
    t = j.get("trust") or {}
    badges = [verdict_badge((elig or {}).get("verdict")), trust_badge(t.get("level"), t.get("score"))]
    if j.get("expired"):
        badges.append(":gray-background[:material/event_busy: **EXPIRED**]")
    for fl in j.get("flags") or []:
        badges.append(f":orange-background[{fl}]")
    st.markdown(" ".join(badges))


def rule_lists_inline(elig: dict[str, Any] | None) -> None:
    """One compact line per group with the rule names, straight from the eligibility engine."""
    if not elig:
        st.caption("Eligibility was not checked for this job.")
        return
    groups: dict[str, list[str]] = {"failed": [], "needs checking": [], "passed": []}
    for r in elig["rules"]:
        groups[STATUS_GROUP[r["status"]]].append(r["rule"])
    st.markdown(
        f":red[**✗ Failed:**] {', '.join(groups['failed']) or 'none'}  \n"
        f":orange[**? Needs checking:**] {', '.join(groups['needs checking']) or 'none'}  \n"
        f":green[**✓ Passed:**] {', '.join(groups['passed']) or 'none'}")


def trust_inline(trust: dict[str, Any] | None) -> None:
    if not trust:
        st.caption("Trust not computed.")
        return
    rs = trust["reasons"]
    st.markdown("**Trust reasons:** " + ("; ".join(f"`{r['code']}` ({r['delta']:+d}) {r['message']}" for r in rs)
                                         if rs else "none (no scam rule or ghost-job signal fired)"))
