"""Paste a job: extracted facts with quotes, eligibility for the current profile and trust; one card per role."""

from __future__ import annotations

import streamlit as st
from lib import (
    APIError,
    api,
    current_profile,
    eligibility_lists,
    facts_with_quotes,
    job_card_header,
    profile_banner,
    show_error,
    trust_reasons,
)

st.title("Paste a job")
try:
    prof = current_profile()
except APIError as e:
    show_error(e)
    st.stop()
profile_banner(prof)
st.caption("Paste the full text of a posting (for example from WhatsApp, email or a college portal). The text is sent "
           "to the LLM provider to extract its requirements. A post listing several roles is split into one job per "
           "role, using only the posting's own lines.")

with st.form("paste"):
    text = st.text_area("Posting text", height=260, max_chars=20000,
                        placeholder="Role: ...\nCompany: ...\nEligibility: ...\nStipend: ...")
    c = st.columns(2)
    company = c[0].text_input("Company (optional)")
    url = c[1].text_input("Link to the posting (optional)")
    go = st.form_submit_button("Extract", icon=":material/search:")

if go:
    if len(text.strip()) < 40:
        st.warning("Please paste the whole posting (at least a few lines).")
    else:
        with st.spinner("Extracting requirements..."):
            try:
                first = api("POST", "/jobs/paste", json={"text": text, "company": company or None,
                                                        "url": url or None}, timeout=240)
                ids = [first["id"], *first.get("related_job_ids", [])]
                st.session_state.pasted = [api("GET", f"/jobs/{i}", params={"profile_id": prof["id"]})
                                           for i in ids]
            except APIError as e:
                show_error(e)

jobs = st.session_state.get("pasted") or []
if len(jobs) > 1:
    st.success(f"This post lists {len(jobs)} roles; each is a separate job below.")
for j in jobs:
    with st.container(border=True):
        job_card_header(j, j.get("eligibility"))
        st.caption(f"Job id {j['id']}")
        tabs = st.tabs(["Extracted facts", "Eligibility", "Trust"])
        with tabs[0]:
            facts_with_quotes(j.get("facts"))
        with tabs[1]:
            eligibility_lists(j.get("eligibility"))
        with tabs[2]:
            t = j.get("trust")
            if t:
                st.metric("Trust score", f"{t['score']}/100", help="Starts at 100; each reason below lowers it")
            trust_reasons(t)
        if st.button("Open in Job detail", key=f"open{j['id']}"):
            st.session_state.job_id = j["id"]
            st.switch_page("pages/job.py")
