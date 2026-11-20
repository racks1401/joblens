"""Job detail: extracted requirements with their evidence quotes, eligibility, trust, and an on-click explanation."""

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

st.title("Job detail")
try:
    prof = current_profile()
except APIError as e:
    show_error(e)
    st.stop()
profile_banner(prof)

jid = st.number_input("Job id", min_value=1, step=1, value=int(st.session_state.get("job_id") or 1),
                      help="Open a job from the Matches page, or type its id.")
st.session_state.job_id = int(jid)

try:
    j = api("GET", f"/jobs/{int(jid)}", params={"profile_id": prof["id"]})
except APIError as e:
    show_error(e)
    st.stop()

with st.container(border=True):
    job_card_header(j, j.get("eligibility"))
    if j.get("url"):
        st.link_button("Open the original posting", j["url"], icon=":material/open_in_new:")
    else:
        st.caption("No link: this job was pasted, not fetched from a job board.")

st.subheader("Extracted requirements")
st.caption("Each value was extracted from the posting by an LLM and kept only if its quote appears in the posting "
           "text. “not stated” means no value with a matching quote was found.")
facts_with_quotes(j.get("facts"))

st.subheader("Eligibility for this profile")
eligibility_lists(j.get("eligibility"))

st.subheader("Trust")
trust_reasons(j.get("trust"))

st.subheader("Explain this match")
st.caption("Calls the LLM only when you click. The explanation text is model-written; skills it names are "
           "checked against your profile and the job's skill lists, and the verdict and trust level are copied from "
           "the rule engine.")
if st.button("Explain this match", icon=":material/auto_awesome:"):
    with st.spinner("Asking the LLM..."):
        try:
            st.session_state[f"expl{jid}"] = api("GET", f"/matches/{prof['id']}/{int(jid)}/explain")
        except APIError as e:
            show_error(e)
ex = st.session_state.get(f"expl{jid}")
if ex:
    left, right = st.columns([3, 2])
    with left:
        if ex.get("explanation_skipped"):
            st.warning(f"The explanation was skipped: {ex.get('reason')}")
        else:
            st.markdown("**Model-written summary**")
            st.info(ex.get("summary") or "")
            st.markdown(f"Matched skills: {', '.join(ex.get('matched_skills') or []) or 'none'}")
            st.markdown(f"Missing skills: {', '.join(ex.get('missing_skills') or []) or 'none'}")
            st.caption(f"Model's own fit word: {ex.get('verdict')} · eligibility (rule engine): "
                       f"{ex.get('eligibility')} · trust (rule engine): {ex.get('trust_level')}"
                       f"{' · from cache' if ex.get('cached') else ''}")
    with right:
        g = ex.get("grounding")
        st.markdown("**Grounding check**")
        if g is None:
            st.caption("Not recorded for this cached explanation.")
        else:
            if g["ok"]:
                st.success("Passed: every skill the model named is in your profile and the job's lists.")
            else:
                st.warning("The model named skills that are not in the inputs; they were removed.")
            st.markdown(f"Dropped from matched: {', '.join(g['matched_dropped']) or 'none'}")
            st.markdown(f"Dropped from missing: {', '.join(g['missing_dropped']) or 'none'}")
            st.caption(g["rule"])
