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


class DailyLimitReached(RuntimeError):
    """Provider says the daily token/request quota is used up; stop and resume tomorrow."""


class TokenPacer:
    """Sliding 60 s window of tokens sent, to stay under a provider tokens-per-minute limit."""

    def __init__(self, tpm: int, clock: Any = time.monotonic, sleep: Any = time.sleep) -> None:
        self.tpm, self.clock, self.sleep = tpm, clock, sleep
        self.events: list[tuple[float, int]] = []

    def wait(self, tokens: int) -> None:
        if self.tpm <= 0:
            return
        tokens = min(tokens, self.tpm)
        while True:
            now = self.clock()
            self.events = [(t, n) for t, n in self.events if now - t < 60]
            used = sum(n for _, n in self.events)
            if used + tokens <= self.tpm:
                return
            self.sleep(max(0.5, 60 - (now - self.events[0][0])))

    def record(self, tokens: int) -> None:
        self.events.append((self.clock(), tokens))


_PACERS: dict[str, TokenPacer] = {}


def shared_pacer(base_url: str, tpm: int) -> TokenPacer:
    """One pacer per provider endpoint: the limit is per account, not per client."""
    if base_url not in _PACERS:
        _PACERS[base_url] = TokenPacer(tpm)
    return _PACERS[base_url]


def _retry_after(headers: Any) -> float:
    """Seconds to wait from retry-after or x-ratelimit-reset-tokens ('14.8s', '1m26s')."""
    for k in ("retry-after", "x-ratelimit-reset-tokens"):
        v = headers.get(k) if headers else None
        if not v:
            continue
        m = re.fullmatch(r"(?:(\d+)m)?([\d.]+)s?", str(v).strip())
        if m:
            return float(m.group(1) or 0) * 60 + float(m.group(2))
    return 10.0


class OpenAIProvider:
    """OpenAI-compatible provider via the openai SDK with a custom base_url.

    Paces requests under `tpm` tokens/minute, waits on 429 (per-minute limits) and raises
    DailyLimitReached when the provider reports a per-day quota.
    """

    name = "openai"

    def __init__(self, api_key: str, base_url: str, timeout_s: float = 60, max_retries: int = 2,
                 tpm: int = 0, model_params: dict[str, dict[str, Any]] | None = None,
                 rate_limit_retries: int = 8) -> None:
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, base_url=base_url or None, timeout=timeout_s, max_retries=max_retries)
        self.pacer = shared_pacer(base_url, tpm)
        self.model_params = model_params or {}
        self.rate_limit_retries = rate_limit_retries

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
        from openai import RateLimitError

        est = sum(estimate_tokens(str(m.get("content") or "")) for m in messages) + 600
        for attempt in range(self.rate_limit_retries + 1):
            self.pacer.wait(est)
            try:
                res = self._complete_once(model=model, messages=messages, temperature=temperature,
                                          json_mode=json_mode, tools=tools)
            except RateLimitError as e:
                body = str(getattr(e, "message", "") or e)
                if re.search(r"per day|\bTPD\b|\bRPD\b|daily", body, re.I):
                    raise DailyLimitReached(body[:300]) from e
                self.pacer.record(est)
                delay = _retry_after(getattr(e.response, "headers", None)) + 1
                log_event(log, "llm_rate_limited", level=logging.WARNING, model=model, attempt=attempt + 1,
                          delay_s=round(delay, 1))
                if attempt == self.rate_limit_retries:
                    raise
                self.pacer.sleep(delay)
                continue
            self.pacer.record(res.tokens_in + res.tokens_out)
            return res
        raise RuntimeError("unreachable")

    def _complete_once(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float,
        json_mode: bool,
        tools: list[dict[str, Any]] | None,
    ) -> LLMResult:
        kwargs: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if self.model_params.get(model):
            kwargs["extra_body"] = dict(self.model_params[model])
        from openai import BadRequestError

        try:
            resp = self.client.chat.completions.create(**kwargs)
        except BadRequestError as e:
            # Groq rejects JSON-mode output that is not valid JSON; treat it as an invalid attempt
            # (content = the failed generation) so the caller's validate-and-retry loop handles it.
            body = getattr(e, "body", None) or {}
            err = body.get("error", body) if isinstance(body, dict) else {}
            if json_mode and isinstance(err, dict) and err.get("code") == "json_validate_failed":
                log_event(log, "llm_json_validate_failed", level=logging.WARNING, model=model)
                return LLMResult(content=str(err.get("failed_generation") or ""), model=model)
            raise
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
        # Never persist fake outputs: the key would not change when the heuristic code changes.
        return LLMClient(FakeProvider(), "fake-heuristic", MemoryCache(), s.cfg.budget.daily_tokens)
    key, base = s.env.llm_api_key, s.env.llm_base_url
    if role == "judge" and s.env.judge_api_key:
        key, base = s.env.judge_api_key, s.env.judge_base_url or base
    prov = OpenAIProvider(key, base, s.cfg.llm.timeout_s, s.cfg.llm.max_retries, tpm=s.cfg.llm.tpm_limit,
                          model_params=s.cfg.llm.model_params)
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
