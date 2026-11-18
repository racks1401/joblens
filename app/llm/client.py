"""LLM client: chat(), chat_json(schema), chat_tools() over any OpenAI-compatible API.

Every call goes through the cache (key = model|prompt_version|input) and is logged with model,
prompt_version, tokens, latency and cache hit. A daily token budget blocks non-essential calls.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel

from app.llm.cache import CacheStore, MemoryCache, cache_key
from app.logs import log_event

log = logging.getLogger("joblens.llm")


class BudgetExceeded(RuntimeError):
    """Daily token budget used up; non-essential call skipped."""


class LLMJSONError(ValueError):
    """Model output was not parseable JSON."""


@dataclass
class ToolCall:
    """One tool call requested by the model."""

    id: str
    name: str
    arguments: str  # raw JSON string as sent by the model

    def args(self) -> dict[str, Any]:
        """Parsed arguments ({} if invalid JSON; validation happens in the tool layer)."""
        try:
            v = json.loads(self.arguments or "{}")
            return v if isinstance(v, dict) else {}
        except json.JSONDecodeError:
            return {}


@dataclass
class LLMResult:
    """Normalized completion result."""

    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cached: bool = False
    latency_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "tool_calls": [{"id": t.id, "name": t.name, "arguments": t.arguments} for t in self.tool_calls],
            "model": self.model,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LLMResult:
        return cls(
            content=d.get("content"),
            tool_calls=[ToolCall(**t) for t in d.get("tool_calls", [])],
            model=d.get("model", ""),
            tokens_in=d.get("tokens_in", 0),
            tokens_out=d.get("tokens_out", 0),
        )

    def assistant_message(self) -> dict[str, Any]:
        """The assistant message to append to the conversation (OpenAI format)."""
        msg: dict[str, Any] = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            msg["tool_calls"] = [
                {"id": t.id, "type": "function", "function": {"name": t.name, "arguments": t.arguments}}
                for t in self.tool_calls
            ]
        return msg


class Provider(Protocol):
    """Backend that performs a completion."""

    name: str

    def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float,
        json_mode: bool,
        tools: list[dict[str, Any]] | None,
        task: str,
    ) -> LLMResult: ...


class OpenAIProvider:
    """OpenAI-compatible provider via the openai SDK with a custom base_url."""

    name = "openai"

    def __init__(self, api_key: str, base_url: str, timeout_s: float = 60, max_retries: int = 2) -> None:
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, base_url=base_url or None, timeout=timeout_s, max_retries=max_retries)

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
        kwargs: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        resp = self.client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        calls = [
            ToolCall(id=c.id, name=c.function.name, arguments=c.function.arguments or "{}")
            for c in (msg.tool_calls or [])
        ]
        usage = resp.usage
        return LLMResult(
            content=msg.content,
            tool_calls=calls,
            model=resp.model or model,
            tokens_in=getattr(usage, "prompt_tokens", 0) or 0,
            tokens_out=getattr(usage, "completion_tokens", 0) or 0,
        )


def parse_json_text(text: str | None) -> dict[str, Any]:
    """Parse a JSON object from model output, tolerating code fences and surrounding prose."""
    if not text:
        raise LLMJSONError("empty response")
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        v = json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, re.S)
        if not m:
            raise LLMJSONError("no JSON object found") from None
        try:
            v = json.loads(m.group(0))
        except json.JSONDecodeError as e:
            raise LLMJSONError(str(e)) from e
    if not isinstance(v, dict):
        raise LLMJSONError("JSON is not an object")
    return v


def estimate_tokens(text: str) -> int:
    """Cheap token estimate (~4 chars per token)."""
    return max(1, len(text) // 4)


class LLMClient:
    """Cached, logged, budgeted LLM client bound to a default model."""

    def __init__(
        self, provider: Provider, model: str, cache: CacheStore | None = None, daily_token_budget: int = 0
    ) -> None:
        self.provider = provider
        self.model = model
        self.cache: CacheStore = cache or MemoryCache()
        self.daily_token_budget = daily_token_budget

    def _call(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None,
        temperature: float,
        prompt_version: str,
        json_mode: bool,
        tools: list[dict[str, Any]] | None,
        essential: bool,
    ) -> LLMResult:
        model = model or self.model
        payload = {"messages": messages, "temperature": temperature, "json": json_mode, "tools": tools}
        key = cache_key(model, prompt_version, payload)
        t0 = time.perf_counter()
        hit = self.cache.get(key)
        if hit is not None:
            res = LLMResult.from_dict(hit)
            res.cached, res.latency_ms = True, (time.perf_counter() - t0) * 1000
        else:
            if not essential and self.daily_token_budget and self.cache.tokens_today() >= self.daily_token_budget:
                log_event(log, "llm_budget_exceeded", level=logging.WARNING, prompt_version=prompt_version)
                raise BudgetExceeded("Daily LLM token budget reached; this optional step was skipped.")
            res = self.provider.complete(
                model=model, messages=messages, temperature=temperature, json_mode=json_mode, tools=tools,
                task=prompt_version,
            )
            res.latency_ms = (time.perf_counter() - t0) * 1000
            self.cache.put(key, model, prompt_version, res.to_dict(), res.tokens_in, res.tokens_out)
        log_event(
            log, "llm_call", provider=self.provider.name, model=model, prompt_version=prompt_version,
            tokens_in=res.tokens_in, tokens_out=res.tokens_out, latency_ms=round(res.latency_ms, 1), cache_hit=res.cached,
        )
        return res

    def chat(
        self, messages: list[dict[str, Any]], *, model: str | None = None, temperature: float = 0.0,
        prompt_version: str = "chat", essential: bool = True,
    ) -> LLMResult:
        """Plain chat completion."""
        return self._call(messages, model=model, temperature=temperature, prompt_version=prompt_version,
                          json_mode=False, tools=None, essential=essential)

    def chat_json(
        self, messages: list[dict[str, Any]], schema: type[BaseModel] | None = None, *, model: str | None = None,
        temperature: float = 0.0, prompt_version: str = "json", essential: bool = True,
    ) -> tuple[dict[str, Any], LLMResult]:
        """JSON-mode completion. Returns (parsed dict, result). Schema validation is the caller's job
        (so it can retry with the validation error); the schema is part of the prompt text."""
        res = self._call(messages, model=model, temperature=temperature, prompt_version=prompt_version,
                         json_mode=True, tools=None, essential=essential)
        return parse_json_text(res.content), res

    def chat_tools(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], *, model: str | None = None,
        temperature: float = 0.2, prompt_version: str = "agent", essential: bool = True,
    ) -> LLMResult:
        """Tool-calling completion."""
        return self._call(messages, model=model, temperature=temperature, prompt_version=prompt_version,
                          json_mode=False, tools=tools, essential=essential)


def get_llm(role: str = "extract", cache: CacheStore | None = None) -> LLMClient:
    """Build the client for a role (extract | explain | agent | judge) from settings.

    Falls back to the offline FakeProvider when no API key is configured.
    """
    from app.config import get_settings
    from app.llm.fake import FakeProvider

    s = get_settings()
    model = getattr(s.cfg.models, role)
    if cache is None:
        try:
            from app.db import session_factory

            cache = DBCacheOrMemory(session_factory())
        except Exception:  # noqa: BLE001
            cache = MemoryCache()
    if s.llm_provider == "fake":
        return LLMClient(FakeProvider(), "fake-heuristic", cache, s.cfg.budget.daily_tokens)
    key, base = s.env.llm_api_key, s.env.llm_base_url
    if role == "judge" and s.env.judge_api_key:
        key, base = s.env.judge_api_key, s.env.judge_base_url or base
    prov = OpenAIProvider(key, base, s.cfg.llm.timeout_s, s.cfg.llm.max_retries)
    return LLMClient(prov, model, cache, s.cfg.budget.daily_tokens)


class DBCacheOrMemory:
    """DB cache that silently degrades to memory if the DB is unreachable."""

    def __init__(self, sf: Any) -> None:
        from app.llm.cache import DBCache

        self.db = DBCache(sf)
        self.mem = MemoryCache()
        self.ok = True

    def _try(self, fn: str, *a: Any) -> Any:
        if self.ok:
            try:
                return getattr(self.db, fn)(*a)
            except Exception:  # noqa: BLE001
                self.ok = False
                log_event(log, "llm_cache_db_unavailable", level=logging.WARNING)
        return getattr(self.mem, fn)(*a)

    def get(self, key: str) -> dict[str, Any] | None:
        return self._try("get", key)

    def put(self, key: str, model: str, pv: str, response: dict[str, Any], ti: int, to: int) -> None:
        self._try("put", key, model, pv, response, ti, to)

    def tokens_today(self) -> int:
        return int(self._try("tokens_today"))
