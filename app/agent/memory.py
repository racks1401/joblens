"""Conversation memory (spec 14.4): last N messages, long-term user_memory, 120-word session summary."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.extract.prompting import build_messages
from app.llm.client import LLMResult
from app.models import ChatMessage, ChatSession, UserMemory


def get_or_create_session(s: Session, profile_id: uuid.UUID, session_id: uuid.UUID | None) -> ChatSession:
    if session_id is not None:
        cs = s.get(ChatSession, session_id)
        if cs is not None and cs.profile_id == profile_id:
            return cs
    cs = ChatSession(id=session_id or uuid.uuid4(), profile_id=profile_id)
    s.add(cs)
    s.commit()
    return cs


def last_messages(s: Session, session_id: uuid.UUID, n: int) -> list[dict[str, str]]:
    """Last n user/assistant messages as plain text (tool traces are not replayed, to keep prompts small)."""
    rows = s.execute(select(ChatMessage).where(ChatMessage.session_id == session_id)
                     .order_by(ChatMessage.id.desc()).limit(n)).scalars().all()
    return [{"role": r.role, "content": str(r.content.get("text") or "")} for r in reversed(rows)]


def save_message(s: Session, session_id: uuid.UUID, role: str, content: dict[str, Any]) -> None:
    s.add(ChatMessage(session_id=session_id, role=role, content=content))
    s.commit()


def message_count(s: Session, session_id: uuid.UUID) -> int:
    return int(s.execute(select(func.count()).select_from(ChatMessage).where(ChatMessage.session_id == session_id)).scalar() or 0)


def maybe_summarize(s: Session, cs: ChatSession, llm: Any, every: int, tokens: dict[str, int]) -> bool:
    """Refresh the session summary every `every` messages (non-essential: skipped on budget/limit errors)."""
    n = message_count(s, cs.id)
    if n == 0 or n % every:
        return False
    msgs = "\n".join(f"{m['role']}: {m['content'][:600]}" for m in last_messages(s, cs.id, every))
    try:
        res = llm.chat(build_messages("summary_v1", previous_summary=cs.summary or "none", messages=msgs),
                       temperature=0.0, prompt_version="summary_v1", essential=False)
    except Exception:  # noqa: BLE001 - a missing summary never breaks the turn
        return False
    tokens["in"] += res.tokens_in
    tokens["out"] += res.tokens_out
    cs.summary = " ".join((res.content or "").split()[:120])
    s.commit()
    return True


def list_memory(s: Session, profile_id: uuid.UUID) -> dict[str, Any]:
    return {m.key: m.value for m in s.execute(select(UserMemory).where(UserMemory.profile_id == profile_id)).scalars()}


def delete_memory(s: Session, profile_id: uuid.UUID, key: str | None = None) -> int:
    q = delete(UserMemory).where(UserMemory.profile_id == profile_id)
    if key is not None:
        q = q.where(UserMemory.key == key)
    n = s.execute(q).rowcount
    s.commit()
    return int(n or 0)


def fake_summary_handler(messages: list[dict[str, Any]], tools: Any) -> LLMResult:
    text = str(messages[-1].get("content") or "")
    users = [ln[6:] for ln in text.splitlines() if ln.startswith("user: ")]
    return LLMResult(content=("User asked: " + "; ".join(users))[:600])
