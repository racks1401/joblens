"""Profile: editable form plus optional PDF resume upload that pre-fills it (the file and its text are not kept)."""

from __future__ import annotations

from typing import Any

import streamlit as st
from lib import DEGREES, EMPLOYMENT_TYPES, WORK_MODES, APIError, api, current_profile, is_demo, show_error

st.title("Profile")
try:
    prof = current_profile()
except APIError as e:
    show_error(e)
    st.stop()

if is_demo(prof):
    st.info("You are using the **synthetic demo profile** (a made-up fresher). Saving the form creates your own "
            "profile for this browser session; the demo profile itself cannot be changed.")
else:
    st.success("You are using your own saved profile.")

st.subheader("Fill from a resume (optional)")
st.caption("PDF only, 5 MB at most. **The resume text is sent to the LLM provider to build a draft profile.** The "
           "file and its text are not stored by JobLens: only the fields you review and save below are kept.")
up = st.file_uploader("Resume PDF", type=["pdf"], key=f"cv{st.session_state.get('cv_key', 0)}")
if up is not None and st.button("Build a draft from this PDF", icon=":material/upload_file:"):
    data = up.getvalue()
    if len(data) > 5 * 1024 * 1024:
        st.error("The PDF is larger than 5 MB.")
    else:
        with st.spinner("Reading the resume..."):
            try:
                d = api("POST", "/profiles/from-resume", files={"file": ("resume.pdf", data, "application/pdf")})
                st.session_state.draft = d["draft"]
                st.success("Draft ready: review every field below, then save.")
            except APIError as e:
                show_error(e)
    del data
    st.session_state.cv_key = st.session_state.get("cv_key", 0) + 1  # drop the uploaded file from the widget

draft: dict[str, Any] = st.session_state.get("draft") or {}
p = prof["profile"]


def val(k: str, default: Any) -> Any:
    v = draft.get(k)
    return v if v not in (None, [], "") else p.get(k, default)


st.subheader("Your details")
with st.form("profile"):
    c = st.columns(3)
    deg = val("degree", "BTECH")
    degree = c[0].selectbox("Degree", DEGREES, index=DEGREES.index(deg) if deg in DEGREES else 0)
    branch = c[1].text_input("Branch / specialization", value=val("specialization", "") or "")
    year = c[2].number_input("Batch (graduation year)", min_value=2015, max_value=2035, step=1,
                             value=int(val("graduation_year", 2026)))
    c = st.columns(3)
    cg = val("cgpa", None)
    cgpa = c[0].number_input("CGPA (0-10, leave 0 if not known)", min_value=0.0, max_value=10.0, step=0.01,
                             value=float(cg) if cg is not None else 0.0)
    exp = c[1].number_input("Experience (years)", min_value=0.0, max_value=40.0, step=0.5,
                            value=float(val("experience_years", 0) or 0))
    minpay = c[2].number_input("Minimum pay (₹ per month, 0 = none)", min_value=0, step=1000,
                               value=int(val("min_monthly_pay_inr", 0) or 0))
    skills = st.text_input("Skills (comma separated)",
                           value=", ".join(s["name"] for s in val("skills", [])))
    roles = st.text_input("Target roles (comma separated)", value=", ".join(val("target_roles", [])))
    cities = st.text_input("Preferred cities (comma separated)", value=", ".join(val("preferred_locations", [])))
    c = st.columns(2)
    modes = c[0].multiselect("Work modes", WORK_MODES, default=[m for m in val("work_modes", WORK_MODES)
                                                              if m in WORK_MODES])
    types = c[1].multiselect("Job types", EMPLOYMENT_TYPES,
                             default=[t for t in val("employment_types", ["internship", "full_time"])
                                      if t in EMPLOYMENT_TYPES])
    save = st.form_submit_button("Save profile", icon=":material/save:")


def split(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


if save:
    body = {"name": None, "degree": degree, "specialization": branch or None, "graduation_year": int(year),
            "cgpa": round(cgpa, 2) or None, "experience_years": exp, "skills": [{"name": s} for s in split(skills)],
            "target_roles": split(roles), "preferred_locations": split(cities), "work_modes": modes or WORK_MODES,
            "employment_types": types or ["internship", "full_time"], "min_monthly_pay_inr": int(minpay) or None}
    if not body["skills"] or not body["target_roles"]:
        st.warning("Please add at least one skill and one target role.")
    else:
        try:
            if is_demo(prof):
                out = api("POST", "/profiles", json=body)
            else:
                out = api("PUT", f"/profiles/{prof['id']}", json=body)
            st.session_state.profile_id = out["id"]
            st.session_state.pop("draft", None)
            st.success("Saved. Matches, Paste and Chat now use this profile.")
        except APIError as e:
            show_error(e)

if not is_demo(prof) and st.button("Go back to the demo profile"):
    st.session_state.pop("profile_id", None)
    st.rerun()
