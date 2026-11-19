"""Split a pasted post that lists several roles into one posting per role.

The LLM returns ONLY line ranges (shared header, shared footer, each role's start/end and heading line).
The code validates them and builds every piece from the original lines: header + role + footer, so the
text is never rewritten. A role without a heading line gets the title "Needs human title" (never named
by the model). Any invalid or single-role answer means "do not split".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.extract.prompting import build_messages
from app.llm.client import LLMClient, LLMJSONError
from app.logs import log_event

log = logging.getLogger("joblens.split")
NEEDS_HUMAN_TITLE = "Needs human title"
PROMPT = "split_v1"


@dataclass
class RolePiece:
    index: int  # 1-based role number in the post
    title: str  # heading line text, or NEEDS_HUMAN_TITLE
    text: str  # header + role + footer, original lines
    lines: tuple[int, int]  # role line range (1-based, inclusive)


def numbered(text: str) -> str:
    return "\n".join(f"{i}| {ln}" for i, ln in enumerate(text.split("\n"), 1))


def _range(v: Any, n: int) -> tuple[int, int] | None:
    if v is None:
        return None
    if not (isinstance(v, list | tuple) and len(v) == 2 and all(isinstance(x, int) for x in v)):
        raise ValueError(f"bad range {v!r}")
    a, b = v
    if not 1 <= a <= b <= n:
        raise ValueError(f"range {v!r} outside 1..{n}")
    return a, b


def plan_from_answer(answer: dict[str, Any], n_lines: int) -> tuple[tuple[int, int] | None, tuple[int, int] | None,
                                                                    list[tuple[int, int, int | None]]]:
    """Validate the model's line plan. Raises ValueError if anything is inconsistent."""
    header = _range(answer.get("header"), n_lines)
    footer = _range(answer.get("footer"), n_lines)
    roles: list[tuple[int, int, int | None]] = []
    for r in answer.get("roles") or []:
        a, b = _range([r.get("start"), r.get("end")], n_lines) or (0, 0)
        h = r.get("heading")
        if h is not None and not (isinstance(h, int) and a <= h <= b):
            raise ValueError(f"heading {h!r} outside its role {a}..{b}")
        roles.append((a, b, h))
    roles.sort()
    for (_, b1, _), (a2, _, _) in zip(roles, roles[1:], strict=False):
        if a2 <= b1:
            raise ValueError("roles overlap")
    if roles and header and header[1] >= roles[0][0]:
        raise ValueError("header overlaps the first role")
    if roles and footer and footer[0] <= roles[-1][1]:
        raise ValueError("footer overlaps the last role")
    return header, footer, roles


def build_pieces(text: str, header: tuple[int, int] | None, footer: tuple[int, int] | None,
                 roles: list[tuple[int, int, int | None]]) -> list[RolePiece]:
    lines = text.split("\n")

    def take(r: tuple[int, int] | None) -> list[str]:
        return lines[r[0] - 1:r[1]] if r else []

    out = []
    for i, (a, b, h) in enumerate(roles, 1):
        title = lines[h - 1].strip().strip("*").rstrip(":").strip() if h else NEEDS_HUMAN_TITLE
        body = take(header) + [""] + take((a, b)) + ([""] + take(footer) if footer else [])
        out.append(RolePiece(i, title or NEEDS_HUMAN_TITLE, "\n".join(body).strip("\n"), (a, b)))
    return out


def split_post(text: str, llm: LLMClient) -> list[RolePiece]:
    """One RolePiece per role, or [] when the post has a single role or the plan is invalid."""
    n = len(text.split("\n"))
    try:
        answer, _ = llm.chat_json(build_messages(PROMPT, numbered_post=numbered(text)), temperature=0.0,
                                  prompt_version=PROMPT)
        header, footer, roles = plan_from_answer(answer, n)
    except (LLMJSONError, ValueError, TypeError) as e:
        log_event(log, "split_rejected", level=logging.WARNING, error=str(e)[:200])
        return []
    if len(roles) < 2:
        return []
    return build_pieces(text, header, footer, roles)
