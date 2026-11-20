"""Profiles: from-resume draft, create, read, update, delete (cascades chats and memory), the demo profile.

Resume privacy: the uploaded PDF and its text are used only to build the draft. Neither is stored or logged; the
draft LLM call uses an in-memory cache so no resume-derived output lands in llm_cache either."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel
from pypdf.errors import PdfReadError
from sqlalchemy.orm import Session

from app.api.errors import APIError
from app.api.limits import enforce
from app.db import get_session
from app.demo import DEMO_PROFILE_ID, ensure_demo_profile
from app.extract.profile import ResumeTextTooShort, extract_profile, normalize_profile, pdf_to_text
from app.models import ProfileRow
from app.schemas import Profile, ProfileDraft

router = APIRouter(tags=["profiles"])
MAX_PDF_BYTES = 5 * 1024 * 1024


class ProfileIn(Profile):
    """Confirmed profile. A resume_text field is accepted for older clients but never stored."""

    resume_text: str | None = None


class ProfileOut(BaseModel):
    id: uuid.UUID
    profile: Profile
    created_at: datetime
    updated_at: datetime


class DraftOut(BaseModel):
    draft: ProfileDraft
    note: str = ("Draft only - review every field, then POST /profiles to save. The resume text was sent to the LLM "
                 "provider to build this draft and was not stored.")


def load_profile(s: Session, profile_id: uuid.UUID) -> tuple[ProfileRow, Profile]:
    """Fetch a saved profile or raise 404."""
    row = s.get(ProfileRow, profile_id)
    if row is None:
        raise APIError(404, "profile_not_found", f"No profile with id {profile_id}")
    return row, Profile.model_validate(row.data)


def _out(row: ProfileRow) -> ProfileOut:
    return ProfileOut(id=row.id, profile=Profile.model_validate(row.data), created_at=row.created_at,
                      updated_at=row.updated_at)


@router.post("/profiles/from-resume", response_model=DraftOut,
             dependencies=[Depends(enforce("resume", purpose="profile"))])
def profile_from_resume(file: UploadFile = File(...)) -> DraftOut:
    """Upload a PDF resume (PDF only, 5 MB max); returns a draft profile. Nothing from the file is stored."""
    from app.llm.cache import MemoryCache
    from app.llm.client import get_llm

    data = file.file.read(MAX_PDF_BYTES + 1)
    if len(data) > MAX_PDF_BYTES:
        raise APIError(413, "file_too_large", "PDF must be 5 MB or smaller")
    if not data.startswith(b"%PDF"):
        raise APIError(415, "not_a_pdf", "Only PDF files are accepted")
    try:
        text = pdf_to_text(data)
    except (PdfReadError, ValueError) as e:
        raise APIError(400, "invalid_pdf", f"Could not read PDF: {type(e).__name__}") from e
    finally:
        del data
    try:
        draft = extract_profile(text, get_llm("profile", cache=MemoryCache()))
    except ResumeTextTooShort as e:
        raise APIError(422, "resume_text_too_short", str(e)) from e
    finally:
        del text
    return DraftOut(draft=draft)


@router.get("/profiles/demo", response_model=ProfileOut)
def demo_profile(s: Session = Depends(get_session)) -> ProfileOut:
    """The synthetic demo profile (created on first use, reset to its fixed data)."""
    return _out(ensure_demo_profile(s))


@router.post("/profiles", response_model=ProfileOut, status_code=201)
def create_profile(body: ProfileIn, s: Session = Depends(get_session)) -> ProfileOut:
    p = normalize_profile(Profile.model_validate(body.model_dump(exclude={"resume_text"})))
    now = datetime.now(UTC)
    row = ProfileRow(id=uuid.uuid4(), data=p.model_dump(mode="json"), resume_text=None, created_at=now, updated_at=now)
    s.add(row)
    s.commit()
    return _out(row)


@router.get("/profiles/{profile_id}", response_model=ProfileOut)
def get_profile(profile_id: uuid.UUID, s: Session = Depends(get_session)) -> ProfileOut:
    return _out(load_profile(s, profile_id)[0])


@router.put("/profiles/{profile_id}", response_model=ProfileOut)
def update_profile(profile_id: uuid.UUID, body: ProfileIn, s: Session = Depends(get_session)) -> ProfileOut:
    if profile_id == DEMO_PROFILE_ID:
        raise APIError(403, "demo_profile_read_only", "The demo profile cannot be changed; save your own profile instead")
    row, _ = load_profile(s, profile_id)
    row.data = normalize_profile(Profile.model_validate(body.model_dump(exclude={"resume_text"}))).model_dump(mode="json")
    row.updated_at = datetime.now(UTC)
    s.commit()
    return _out(row)


@router.delete("/profiles/{profile_id}")
def delete_profile(profile_id: uuid.UUID, s: Session = Depends(get_session)) -> dict[str, Any]:
    """Delete the profile, its explanations, chats and memory (FK cascades)."""
    if profile_id == DEMO_PROFILE_ID:
        raise APIError(403, "demo_profile_read_only", "The demo profile cannot be deleted")
    row, _ = load_profile(s, profile_id)
    s.delete(row)
    s.commit()
    return {"deleted": str(profile_id)}
