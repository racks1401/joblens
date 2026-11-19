"""LLM response cache. Key = sha256(model|prompt_version|input). Backed by the llm_cache table or memory."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.models import LLMCacheRow


def cache_key(model: str, prompt_version: str, payload: Any) -> str:
    """Stable hash of the call inputs."""
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(f"{model}|{prompt_version}|{blob}".encode()).hexdigest()


class CacheStore(Protocol):
    """Where cached responses live."""

    def get(self, key: str) -> dict[str, Any] | None: ...

    def put(
        self, key: str, model: str, prompt_version: str, response: dict[str, Any], tokens_in: int, tokens_out: int
    ) -> None: ...

    def tokens_today(self, purpose: str | None = None) -> int: ...


def purpose_of(prompt_version: str) -> str:
    """Budget purpose of a call: the prompt name without its version ("agent_v1" -> "agent")."""
    return re.sub(r"_v\d+$", "", prompt_version or "")


class MemoryCache:
    """In-process cache (tests, evals without DB)."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self._tokens: dict[tuple[str, str], int] = {}

    def get(self, key: str) -> dict[str, Any] | None:
        row = self.rows.get(key)
        return row["response"] if row else None

    def put(
        self, key: str, model: str, prompt_version: str, response: dict[str, Any], tokens_in: int, tokens_out: int
    ) -> None:
        self.rows[key] = {"model": model, "prompt_version": prompt_version, "response": response}
        k = (datetime.now(UTC).date().isoformat(), purpose_of(prompt_version))
        self._tokens[k] = self._tokens.get(k, 0) + tokens_in + tokens_out

    def tokens_today(self, purpose: str | None = None) -> int:
        day = datetime.now(UTC).date().isoformat()
        return sum(v for (d, p), v in self._tokens.items() if d == day and (purpose is None or p == purpose))


class DBCache:
    """llm_cache table. Rows are only written for real (non-cached) calls, so their tokens measure spend."""

    def __init__(self, sf: sessionmaker[Session]) -> None:
        self.sf = sf

    def get(self, key: str) -> dict[str, Any] | None:
        with self.sf() as s:
            row = s.get(LLMCacheRow, key)
            return row.response if row else None

    def put(
        self, key: str, model: str, prompt_version: str, response: dict[str, Any], tokens_in: int, tokens_out: int
    ) -> None:
        with self.sf() as s:
            stmt = insert(LLMCacheRow).values(
                key=key, model=model, prompt_version=prompt_version, response=response,
                tokens_in=tokens_in, tokens_out=tokens_out,
            ).on_conflict_do_nothing(index_elements=["key"])
            s.execute(stmt)
            s.commit()

    def tokens_today(self, purpose: str | None = None) -> int:
        start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        with self.sf() as s:
            q = select(func.coalesce(func.sum(LLMCacheRow.tokens_in + LLMCacheRow.tokens_out), 0)).where(
                LLMCacheRow.created_at >= start
            )
            if purpose is not None:  # "agent" matches agent_v1, agent_v2 ... but not other prompts
                q = q.where(LLMCacheRow.prompt_version.op("~")(f"^{re.escape(purpose)}(_v[0-9]+)?$"))
            return int(s.execute(q).scalar() or 0)
