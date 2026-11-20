"""Demo rate limits for actions that call an LLM: chat, paste, explain, resume.

Each action has a per-client-session and a whole-app limit per UTC day (settings `limits.actions`), stored in
`usage_counts` so restarts do not reset them. The client session is the X-Session-Id header (the UI sends one per
browser session); requests without it share one "anonymous" bucket. Before counting, the per-purpose daily token
budget (settings `budget`) is checked, so a used-up budget gives the same clear 429 message instead of a half run.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Header
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.api.errors import APIError
from app.db import get_session
from app.models import UsageCount

ANON = "anonymous"


def _today() -> datetime:
    return datetime.now(UTC)


def budget_left(purpose: str) -> tuple[int, int]:
    """(tokens used today, daily budget) for a purpose, from the same counters the LLM client enforces."""
    from app.config import get_settings
    from app.llm.client import get_llm

    budget = get_settings().cfg.budget.for_purpose(purpose)
    used = get_llm("agent").cache.tokens_today(purpose)
    return used, budget


def _bump(s: Session, action: str, key: str) -> int:
    day = _today().date()
    stmt = (insert(UsageCount).values(day=day, action=action, key=key, count=1)
            .on_conflict_do_update(index_elements=["day", "action", "key"], set_={"count": UsageCount.count + 1})
            .returning(UsageCount.count))
    n = int(s.execute(stmt).scalar_one())
    s.commit()
    return n


def usage(s: Session, action: str, key: str) -> int:
    row = s.get(UsageCount, {"day": _today().date(), "action": action, "key": key})
    return row.count if row else 0


def enforce(action: str, purpose: str | None = None) -> Callable[..., None]:
    """FastAPI dependency: refuse with 429 when the action's limit or its token budget is used up."""

    def dep(x_session_id: str | None = Header(default=None), s: Session = Depends(get_session)) -> None:
        from app.config import get_settings

        st = get_settings()
        cfg = st.cfg.limits
        if not (cfg.enabled and st.env.demo_limits):
            return
        if purpose:
            used, budget = budget_left(purpose)
            if budget and used >= budget:
                raise APIError(429, "daily_budget_used",
                               f"Today's LLM token budget for {purpose} is used up ({used:,} of {budget:,} tokens). "
                               "It resets at 00:00 UTC.")
        lim = cfg.for_action(action)
        key = (x_session_id or ANON).strip()[:64] or ANON
        if usage(s, action, "*") >= lim.per_day:
            raise APIError(429, "daily_limit_reached",
                           f"The demo's daily limit for {action} ({lim.per_day}) is used up. It resets at 00:00 UTC.")
        if usage(s, action, key) >= lim.per_session:
            raise APIError(429, "session_limit_reached",
                           f"This session has used its {lim.per_session} {action} requests for today.")
        _bump(s, action, "*")
        _bump(s, action, key)

    return dep


router = APIRouter(tags=["limits"])

ACTION_PURPOSE = {"chat": "agent", "paste": "facts", "explain": "explain", "resume": "profile"}


@router.get("/limits")
def limits_route(x_session_id: str | None = Header(default=None), s: Session = Depends(get_session)) -> dict[str, Any]:
    """Today's use per action for this client session and the whole app, plus the token budgets."""
    from app.config import get_settings

    st = get_settings()
    cfg = st.cfg.limits
    key = (x_session_id or ANON).strip()[:64] or ANON
    out: dict[str, Any] = {"enabled": cfg.enabled and st.env.demo_limits, "resets": "00:00 UTC", "actions": {}}
    for action, purpose in ACTION_PURPOSE.items():
        lim = cfg.for_action(action)
        used, budget = budget_left(purpose)
        out["actions"][action] = {"session_used": usage(s, action, key), "per_session": lim.per_session,
                                  "day_used": usage(s, action, "*"), "per_day": lim.per_day,
                                  "budget_purpose": purpose, "tokens_used": used, "token_budget": budget}
    return out
