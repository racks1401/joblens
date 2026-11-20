"""Chat: the job agent, with its grounding check and tool calls under each answer, and the known limitations."""

from __future__ import annotations

import json
from typing import Any

import streamlit as st
from lib import APIError, api, current_profile, profile_banner, show_error

st.title("Chat")
try:
    prof = current_profile()
except APIError as e:
    show_error(e)
    st.stop()
profile_banner(prof)

with st.expander("Known limitations: read before trusting an answer", expanded=False, icon=":material/warning:"):
    st.markdown(
        "- Answers are written by an LLM from tool results. A code check (grounding) compares job ids, numbers, "
        "dates, eligibility verdicts, rule names and “not listed” claims with the tool outputs, but it cannot read "
        "free sentences: summary lines can still overgeneralize.\n"
        "- Questions about the whole database (“which job pays the most”) are answered from one search of a few "
        "jobs, not a full ranking.\n"
        "- Most job-board postings have no extracted job type, so a “full-time” filter can drop them silently.\n"
        "- Tested on 15 fixed conversations with one profile and one model; in the last review 4 of 15 answers had "
        "real mistakes, later re-runs fixed some of them (see the Evals page).\n"
        "- The shared demo profile cannot store preferences; save your own profile first.")

key = f"chat_{prof['id']}"
hist: list[dict[str, Any]] = st.session_state.setdefault(key, [])


def show_turn(t: dict[str, Any]) -> None:
    with st.chat_message("user"):
        st.markdown(t["user"])
    with st.chat_message("assistant"):
        r = t.get("result")
        if r is None:
            st.warning(t.get("error", "No answer."))
            return
        st.markdown(r["answer"])
        g = r.get("grounding") or {}
        flags = {k: v for k, v in g.items() if isinstance(v, list) and v and k not in (
            "job_ids_checked", "numbers_checked", "accepted_from_profile", "accepted_from_user_message")}
        if g.get("ok"):
            st.success(f"Grounding check passed: {len(g.get('job_ids_checked', []))} job ids and "
                       f"{len(g.get('numbers_checked', []))} numbers match this turn's tool outputs.",
                       icon=":material/verified:")
        else:
            st.warning("Grounding check found claims not backed by the tool outputs: "
                       + "; ".join(f"{k.replace('_', ' ')}: {json.dumps(v, ensure_ascii=False)[:200]}"
                                   for k, v in flags.items()), icon=":material/report:")
        acc = (g.get("accepted_from_profile") or []) + (g.get("accepted_from_user_message") or [])
        if acc:
            st.caption(f"Accepted because they are your own profile or message values: {', '.join(map(str, acc))}")
        if r.get("stop"):
            st.caption(f"Stopped early: {r['stop']}")
        with st.expander(f"Tool calls ({len(r.get('tool_trace', []))}) · tokens {r.get('tokens', {}).get('total', 0)}"):
            for tr in r.get("tool_trace", []):
                st.markdown(f"**{tr['tool']}** `{json.dumps(tr['args'], ensure_ascii=False)}`"
                            f"{' · error: ' + tr['error'] if tr.get('error') else ''}")
                st.json(tr.get("output"), expanded=False)


for t in hist:
    show_turn(t)

msg = st.chat_input("Ask about jobs, eligibility, pay or trust (for example: remote data roles for my batch)")
if msg:
    turn: dict[str, Any] = {"user": msg}
    with st.spinner("Thinking..."):
        try:
            turn["result"] = api("POST", "/chat", json={"profile_id": prof["id"], "message": msg,
                                                       "session_id": st.session_state.get(f"sid_{prof['id']}")},
                                 timeout=180)
            st.session_state[f"sid_{prof['id']}"] = turn["result"]["session_id"]
        except APIError as e:
            show_error(e)
            turn["error"] = e.message
    hist.append(turn)
    st.rerun()

if hist and st.button("Clear this chat"):
    st.session_state[key] = []
    st.session_state.pop(f"sid_{prof['id']}", None)
    st.rerun()
