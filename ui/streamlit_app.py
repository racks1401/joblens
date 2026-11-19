"""JobLens UI entry point. P7 adds the pages (Profile, Matches, Search, Assistant, Add job, Eval results);
for now this shows the API status line with the cold-start message (spec 17)."""

from __future__ import annotations

import os
import time

import httpx
import streamlit as st

API_URL = os.environ.get("API_URL", "http://localhost:8000")

st.set_page_config(page_title="JobLens", page_icon="🔎", layout="wide")
st.title("JobLens")
st.caption("Fit + trust job matcher for freshers")


@st.cache_data(ttl=15)
def health() -> dict | None:
    try:
        r = httpx.get(f"{API_URL}/health", timeout=10)
        return r.json() if r.status_code == 200 else None
    except httpx.HTTPError:
        return None


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
    db = "✅" if h["db"]["ok"] else "❌"
    st.success(f"Server: {h['status']} · DB {db} (pgvector {h['db']['pgvector']}) · LLM: {h['llm']['provider']}")
st.info("Pages (Profile, Matches, Search, Assistant, Add job, Eval results) are built in phase P7.")
