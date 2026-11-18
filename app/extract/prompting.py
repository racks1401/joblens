"""Prompt file loading and rendering (prompts live in app/extract/prompts/*.txt)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


@lru_cache
def load_prompt(name: str) -> tuple[str, str]:
    """Return (system, user_template). Files without SYSTEM:/USER: markers are all system text."""
    raw = (PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")
    if raw.startswith("SYSTEM:") and "\nUSER:\n" in raw:
        sys_part, user_part = raw[len("SYSTEM:"):].split("\nUSER:\n", 1)
        return sys_part.strip(), user_part
    return raw.strip(), ""


def render(template: str, **values: Any) -> str:
    """Replace {key} placeholders for the given keys only (other braces, e.g. JSON examples, stay)."""
    for k, v in values.items():
        template = template.replace("{" + k + "}", str(v))
    return template


def build_messages(name: str, **values: Any) -> list[dict[str, str]]:
    """System + user messages for a prompt file."""
    system, user = load_prompt(name)
    msgs = [{"role": "system", "content": render(system, **values)}]
    if user:
        msgs.append({"role": "user", "content": render(user, **values)})
    return msgs
