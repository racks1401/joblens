"""Demo rate limits for actions that call an LLM: chat, paste, explain, resume.

Each action has a per-client-session, a per-visitor-IP and a whole-app limit per UTC day (settings
`limits.actions`), stored in `usage_counts` so restarts do not reset them. The client session is the X-Session-Id
header (the UI sends one per browser session); requests without it share one "anonymous" bucket. A new browser
session is one reload away, so the UI also forwards the visitor's X-Forwarded-For as X-Visitor-Forwarded-For (a
separate header, because the API host's own proxy appends to X-Forwarded-For). The visitor IP is the entry
`limits.proxy_hops` places from the right; entries further left are whatever the visitor sent and are ignored.
The IP is stored only as a keyed hash. Before counting, the per-purpose daily token budget (settings `budget`) is
checked, so a used-up budget gives the same clear 429 message instead of a half run.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
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
UNKNOWN_IP = "ip:unknown"


def visitor_ip(forwarded_for: str | None, proxy_hops: int) -> str | None:
    """The visitor IP from an X-Forwarded-For value: the entry `proxy_hops` places from the right, if it is an IP."""
    if not forwarded_for:
        return None
    parts = [p.strip() for p in forwarded_for.split(",") if p.strip()]
    if proxy_hops < 1 or len(parts) < proxy_hops:
        return None
    try:
        return str(ipaddress.ip_address(parts[-proxy_hops]))
    except ValueError:
        return None


def ip_key(ip: str | None, public_demo: bool) -> str | None:
    """usage_counts key for a visitor IP (a keyed hash, never the raw address). Without an IP the limit is skipped
    locally; in public-demo mode such requests share one bucket instead."""
    if ip is None:
        return UNKNOWN_IP if public_demo else None
    salt = os.environ.get("UI_API_KEY", "") or "joblens"
    return "ip:" + hmac.new(salt.encode(), ip.encode(), hashlib.sha256).hexdigest()[:20]


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

    def dep(x_session_id: str | None = Header(default=None), x_visitor_forwarded_for: str | None = Header(default=None),
            s: Session = Depends(get_session)) -> None:
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
        ipk = ip_key(visitor_ip(x_visitor_forwarded_for, cfg.proxy_hops), st.env.public_demo) if lim.per_ip else None
        if ipk and usage(s, action, ipk) >= lim.per_ip:
            raise APIError(429, "ip_limit_reached",
                           f"Your network has used its {lim.per_ip} {action} requests for today. "
                           "It resets at 00:00 UTC.")
        if usage(s, action, key) >= lim.per_session:
            raise APIError(429, "session_limit_reached",
                           f"This session has used its {lim.per_session} {action} requests for today.")
        _bump(s, action, "*")
        if ipk:
            _bump(s, action, ipk)
        _bump(s, action, key)

    return dep


router = APIRouter(tags=["limits"])

ACTION_PURPOSE = {"chat": "agent", "paste": "facts", "explain": "explain", "resume": "profile"}


@router.get("/limits")
def limits_route(x_session_id: str | None = Header(default=None),
                 x_visitor_forwarded_for: str | None = Header(default=None),
                 s: Session = Depends(get_session)) -> dict[str, Any]:
    """Today's use per action for this client session, its visitor IP and the whole app, plus the token budgets."""
    from app.config import get_settings

    st = get_settings()
    cfg = st.cfg.limits
    key = (x_session_id or ANON).strip()[:64] or ANON
    ipk = ip_key(visitor_ip(x_visitor_forwarded_for, cfg.proxy_hops), st.env.public_demo)
    out: dict[str, Any] = {"enabled": cfg.enabled and st.env.demo_limits, "resets": "00:00 UTC",
                           "public_demo": st.env.public_demo, "actions": {}}
    for action, purpose in ACTION_PURPOSE.items():
        lim = cfg.for_action(action)
        used, budget = budget_left(purpose)
        out["actions"][action] = {"session_used": usage(s, action, key), "per_session": lim.per_session,
                                  "ip_used": usage(s, action, ipk) if ipk else None, "per_ip": lim.per_ip,
                                  "day_used": usage(s, action, "*"), "per_day": lim.per_day,
                                  "budget_purpose": purpose, "tokens_used": used, "token_budget": budget}
    return out
