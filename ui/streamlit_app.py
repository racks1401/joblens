"""JobLens UI: page navigation plus the API status line (with the cold-start wait for free hosting)."""

from __future__ import annotations

import time

import httpx
import streamlit as st
from lib import API_URL

st.set_page_config(page_title="JobLens", page_icon=":material/travel_explore:", layout="wide")


@st.cache_data(ttl=15)
def health() -> dict | None:
    try:
        r = httpx.get(f"{API_URL}/health", timeout=10)
        return r.json() if r.status_code == 200 else None
    except httpx.HTTPError:
        return None


pages = [
    st.Page("pages/matches.py", title="Matches", icon=":material/work:", default=True),
    st.Page("pages/job.py", title="Job detail", icon=":material/description:"),
    st.Page("pages/paste.py", title="Paste a job", icon=":material/content_paste:"),
    st.Page("pages/profile.py", title="Profile", icon=":material/person:"),
    st.Page("pages/chat.py", title="Chat", icon=":material/chat:"),
    st.Page("pages/evals.py", title="Evals", icon=":material/analytics:"),
]
nav = st.navigation(pages)

with st.sidebar:
    st.markdown("### JobLens")
    st.caption("Fit + trust job matcher for students and freshers in India.")
    h = health()
    if h is None:
        with st.status("API is waking up (free hosting sleeps when idle)...", expanded=True) as s:
            for i in range(30):
                time.sleep(2)
                st.cache_data.clear()
                h = health()
                if h:
                    s.update(label=f"API is up after {2 * (i + 1)} s", state="complete")
                    break
            else:
                s.update(label="API did not respond. Try again in a minute.", state="error")
    if h:
        st.caption(f"API {h['status']} · database {'ok' if h['db']['ok'] else 'down'} · LLM {h['llm']['provider']}")

nav.run()
