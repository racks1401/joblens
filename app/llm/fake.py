"""Offline fake LLM provider.

Used when no LLM_API_KEY is configured and in tests. It routes each call by task (= prompt_version
prefix) to a deterministic, rule-based handler, e.g. the regex JobFacts extractor. Results are labeled
with model "fake-heuristic" everywhere so they are never mistaken for real LLM output.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable
from typing import Any

from app.llm.client import LLMResult, estimate_tokens

Handler = Callable[[list[dict[str, Any]], list[dict[str, Any]] | None], LLMResult]

# task prefix -> "module:function". Resolved lazily so modules can be added phase by phase.
HANDLERS: dict[str, str] = {
    "facts": "app.extract.heuristic:fake_facts_handler",
    "profile": "app.extract.heuristic:fake_profile_handler",
    "explain": "app.explain:fake_explain_handler",
    "judge": "evals.judges:fake_judge_handler",
    "agent": "app.agent.fake_policy:fake_agent_handler",
    "summary": "app.agent.memory:fake_summary_handler",
    "rerank": "app.search.rerank:fake_rerank_handler",
}


class FakeProvider:
    """Deterministic provider; `overrides` lets tests inject handlers per task prefix."""

    name = "fake"

    def __init__(self, overrides: dict[str, Handler] | None = None) -> None:
        self.overrides = overrides or {}
        self.calls: list[dict[str, Any]] = []

    def _resolve(self, task: str) -> Handler | None:
        for prefix, fn in self.overrides.items():
            if task.startswith(prefix):
                return fn
        for prefix, path in HANDLERS.items():
            if task.startswith(prefix):
                mod, attr = path.split(":")
                try:
                    return getattr(importlib.import_module(mod), attr)  # type: ignore[no-any-return]
                except (ImportError, AttributeError):
                    return None
        return None

    def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float,
        json_mode: bool,
        tools: list[dict[str, Any]] | None,
        task: str,
    ) -> LLMResult:
        self.calls.append({"task": task, "messages": messages, "tools": tools})
        handler = self._resolve(task)
        if handler is None:
            content = json.dumps({}) if json_mode else "OK"
            res = LLMResult(content=content)
        else:
            res = handler(messages, tools)
        res.model = model
        res.tokens_in = sum(estimate_tokens(str(m.get("content") or "")) for m in messages)
        res.tokens_out = estimate_tokens(res.content or "") + 10 * len(res.tool_calls)
        return res
