"""Matches: the top matches for the current profile, one card per job, all values from GET /matches."""

from __future__ import annotations

import streamlit as st
from lib import (
    APIError,
    api,
    current_profile,
    eligibility_lists,
    field,
    money,
    profile_banner,
    rule_lists_inline,
    show_error,
    trust_badge,
    trust_inline,
    trust_reasons,
    verdict_badge,
)

st.title("Matches")
try:
    prof = current_profile()
except APIError as e:
    show_error(e)
    st.stop()
profile_banner(prof)

c1, c2 = st.columns([1, 1])
show_inel = c1.toggle("Include INELIGIBLE jobs", value=False)
limit = c2.select_slider("How many", options=[5, 10, 20, 30], value=10)

try:
    res = api("GET", f"/matches/{prof['id']}", params={"limit": limit, "show_ineligible": show_inel})["results"]
except APIError as e:
    show_error(e)
    st.stop()

if not res:
    st.info("No matches for this profile. Try including INELIGIBLE jobs or widening the profile.")
st.caption(f"{len(res)} jobs, ranked by fit score (retrieval, skill overlap, eligibility and trust). "
           "Expired jobs are hidden.")

for it in res:
    with st.container(border=True):
        top = st.columns([5, 2])
        with top[0]:
            st.markdown(f"#### {it['title']}")
            st.markdown(f"**{it['company'] or 'Company not stated'}** · {it['location_raw'] or 'location not stated'}"
                        f" · {field(it['work_mode'])}")
        with top[1]:
            st.markdown(verdict_badge((it.get("eligibility") or {}).get("verdict")))
            t = it.get("trust") or {}
            st.markdown(trust_badge(t.get("level"), t.get("score")))
            if it.get("expired"):
                st.markdown(":gray-background[:material/event_busy: **EXPIRED**]")
        m = st.columns(4)
        m[0].metric("Fit score", it["final_score"], help="0-100, from the ranking formula")
        m[1].markdown(f"**Pay**  \n{money(it.get('pay'))}")
        m[2].markdown(f"**Apply by**  \n{field(it.get('apply_deadline'))}")
        m[3].markdown(f"**Type**  \n{field(it.get('employment_type'))}")
        rule_lists_inline(it.get("eligibility"))
        trust_inline(it.get("trust"))
        with st.expander("Why: the message behind each rule"):
            eligibility_lists(it.get("eligibility"))
            st.markdown("**Trust**")
            trust_reasons(it.get("trust"))
        b = st.columns([1, 1, 4])
        if b[0].button("Details", key=f"d{it['job_id']}", icon=":material/open_in_new:"):
            st.session_state.job_id = it["job_id"]
            st.switch_page("pages/job.py")
        if it.get("url"):
            b[1].link_button("Posting", it["url"])
