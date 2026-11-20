"""The public demo's default profile: a generic, synthetic fresher (not a real person, not an eval profile)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models import ProfileRow
from app.schemas import Profile

DEMO_PROFILE_ID = uuid.UUID("00000000-0000-4000-8000-00000000d3e0")

DEMO_PROFILE = Profile.model_validate({
    "name": "Demo fresher (synthetic)",
    "degree": "BTECH",
    "specialization": "computer science",
    "graduation_year": 2026,
    "cgpa": 7.6,
    "experience_years": 0,
    "skills": [{"name": n} for n in ("python", "sql", "java", "javascript", "git", "html", "css", "data structures")],
    "target_roles": ["software engineer", "data analyst"],
    "preferred_locations": ["Bengaluru", "Hyderabad", "Pune"],
    "work_modes": ["onsite", "hybrid", "remote"],
    "employment_types": ["internship", "full_time"],
})


def ensure_demo_profile(s: Session) -> ProfileRow:
    """Create the demo profile if missing. Its data is reset to DEMO_PROFILE so a shared demo stays generic."""
    row = s.get(ProfileRow, DEMO_PROFILE_ID)
    from app.extract.profile import normalize_profile

    data = normalize_profile(DEMO_PROFILE).model_dump(mode="json")
    if row is None:
        now = datetime.now(UTC)
        row = ProfileRow(id=DEMO_PROFILE_ID, data=data, resume_text=None, created_at=now, updated_at=now)
        s.add(row)
        s.commit()
    elif row.data != data:
        row.data = data
        row.updated_at = datetime.now(UTC)
        s.commit()
    return row
