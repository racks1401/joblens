"""POST /chat, GET/DELETE /memory/{profile_id}[/{key}], GET /matches/{profile_id}/{job_id}/explain (spec 16)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agent import memory as mem
from app.agent.loop import run_turn
from app.agent.tools import JobIdArgs, ToolContext, effective_profile, explain, load_memory
from app.api.errors import APIError
from app.api.limits import enforce
from app.api.routes_profiles import load_profile
from app.db import get_session

router = APIRouter(tags=["chat"])


class ChatIn(BaseModel):
    profile_id: uuid.UUID
    session_id: uuid.UUID | None = None
    message: str = Field(min_length=1, max_length=2000)


@router.post("/chat", dependencies=[Depends(enforce("chat", purpose="agent"))])
def chat_route(body: ChatIn, s: Session = Depends(get_session)) -> dict[str, Any]:
    """One agent turn: {session_id, answer, tool_trace, grounding, tokens, stop}."""
    _, profile = load_profile(s, body.profile_id)
    return run_turn(s, body.profile_id, profile, body.message, body.session_id)


@router.get("/memory/{profile_id}")
def memory_get(profile_id: uuid.UUID, s: Session = Depends(get_session)) -> dict[str, Any]:
    load_profile(s, profile_id)
    return {"profile_id": str(profile_id), "memory": mem.list_memory(s, profile_id)}


@router.delete("/memory/{profile_id}")
def memory_delete_all(profile_id: uuid.UUID, s: Session = Depends(get_session)) -> dict[str, Any]:
    load_profile(s, profile_id)
    return {"deleted": mem.delete_memory(s, profile_id)}


@router.delete("/memory/{profile_id}/{key}")
def memory_delete(profile_id: uuid.UUID, key: str, s: Session = Depends(get_session)) -> dict[str, Any]:
    load_profile(s, profile_id)
    n = mem.delete_memory(s, profile_id, key)
    if not n:
        raise APIError(404, "memory_not_found", f"No memory item {key!r}")
    return {"deleted": n}


@router.get("/matches/{profile_id}/{job_id}/explain", dependencies=[Depends(enforce("explain", purpose="explain"))])
def explain_route(profile_id: uuid.UUID, job_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    _, profile = load_profile(s, profile_id)
    memory = load_memory(s, profile_id)
    ctx = ToolContext(session=s, profile_id=profile_id, profile=effective_profile(profile, memory), memory=memory)
    out = explain(ctx, JobIdArgs(job_id=job_id))
    if out.get("error", "").startswith("no job"):
        raise APIError(404, "job_not_found", out["error"])
    return out
