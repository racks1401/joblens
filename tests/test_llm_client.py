import json
from typing import Any

import pytest

from app.llm.cache import MemoryCache, cache_key
from app.llm.client import BudgetExceeded, LLMClient, LLMJSONError, LLMResult, ToolCall, parse_json_text
from app.llm.fake import FakeProvider


def _json_handler(messages: list[dict[str, Any]], tools: Any) -> LLMResult:
    return LLMResult(content='```json\n{"a": 1}\n```')


def test_cache_key_stable_and_sensitive() -> None:
    assert cache_key("m", "v1", {"x": 1, "y": 2}) == cache_key("m", "v1", {"y": 2, "x": 1})
    assert cache_key("m", "v1", {"x": 1}) != cache_key("m", "v2", {"x": 1})
    assert cache_key("m", "v1", {"x": 1}) != cache_key("m2", "v1", {"x": 1})


def test_chat_json_parses_and_caches() -> None:
    prov = FakeProvider({"t": _json_handler})
    llm = LLMClient(prov, "fake", MemoryCache())
    msgs = [{"role": "user", "content": "hi"}]
    data, r1 = llm.chat_json(msgs, prompt_version="t1")
    assert data == {"a": 1} and not r1.cached
    data2, r2 = llm.chat_json(msgs, prompt_version="t1")
    assert data2 == {"a": 1} and r2.cached
    assert len(prov.calls) == 1  # second call served from cache


def test_parse_json_text_variants() -> None:
    assert parse_json_text('prefix {"k": [1, 2]} suffix') == {"k": [1, 2]}
    with pytest.raises(LLMJSONError):
        parse_json_text("no json here")
    with pytest.raises(LLMJSONError):
        parse_json_text("[1, 2]")


def test_budget_blocks_only_non_essential() -> None:
    cache = MemoryCache()
    cache.put("k", "m", "v", {"content": "x"}, 500, 500)
    llm = LLMClient(FakeProvider(), "fake", cache, daily_token_budget=1000)
    with pytest.raises(BudgetExceeded):
        llm.chat([{"role": "user", "content": "q"}], prompt_version="explain_x", essential=False)
    assert llm.chat([{"role": "user", "content": "q"}], prompt_version="other").content == "OK"


def test_tool_calls_roundtrip_through_cache() -> None:
    def handler(messages: Any, tools: Any) -> LLMResult:
        return LLMResult(content=None, tool_calls=[ToolCall("c1", "search_jobs", json.dumps({"query": "py"}))])

    llm = LLMClient(FakeProvider({"agent": handler}), "fake", MemoryCache())
    tools = [{"type": "function", "function": {"name": "search_jobs", "parameters": {}}}]
    r = llm.chat_tools([{"role": "user", "content": "x"}], tools, prompt_version="agent_v1")
    r2 = llm.chat_tools([{"role": "user", "content": "x"}], tools, prompt_version="agent_v1")
    assert r2.cached and r2.tool_calls[0].args() == {"query": "py"}
    msg = r.assistant_message()
    assert msg["tool_calls"][0]["function"]["name"] == "search_jobs"


def test_bad_tool_args_parse_to_empty() -> None:
    assert ToolCall("1", "x", "{not json").args() == {}


def test_openai_provider_maps_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """The OpenAI provider is exercised against a stubbed SDK client (no network)."""
    from types import SimpleNamespace

    from app.llm.client import OpenAIProvider

    prov = OpenAIProvider("k", "http://localhost:1/v1")
    tc = SimpleNamespace(id="c1", function=SimpleNamespace(name="get_job", arguments='{"job_id": 3}'))
    resp = SimpleNamespace(
        model="m-x",
        choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[tc]))],
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7),
    )
    captured: dict[str, Any] = {}

    def create(**kw: Any) -> Any:
        captured.update(kw)
        return resp

    monkeypatch.setattr(prov.client.chat.completions, "create", create)
    r = prov.complete(model="m", messages=[], temperature=0, json_mode=True, tools=[{"x": 1}], task="t")
    assert r.tool_calls[0].args() == {"job_id": 3} and r.tokens_in == 11 and r.model == "m-x"
    assert captured["response_format"] == {"type": "json_object"} and captured["tool_choice"] == "auto"
