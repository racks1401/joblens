"""Deterministic offline agent policy for the fake LLM (tests and keyless runs). Labeled fake-heuristic.

First step after a user message: pick one tool from simple keywords. After tool results: answer by listing
the jobs from the tool outputs with their job ids and verdicts (no new facts).
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.llm.client import LLMResult, ToolCall


def _call(name: str, args: dict[str, Any]) -> LLMResult:
    return LLMResult(content=None, tool_calls=[ToolCall(id=f"fake_{name}", name=name, arguments=json.dumps(args))])


def fake_agent_handler(messages: list[dict[str, Any]], tools: Any) -> LLMResult:
    last_user = max(i for i, m in enumerate(messages) if m["role"] == "user")
    after = messages[last_user + 1:]
    text = str(messages[last_user]["content"])
    low = text.lower()
    if not after:
        m = re.search(r"remember (?:that )?i prefer (\w[\w ]*)", low)
        if m:
            return _call("remember", {"key": "preferred_locations", "value": [m.group(1).strip().title()]})
        ids = [int(x) for x in re.findall(r"\b(?:job|id)\s*#?(\d+)", low)]
        if "compare" in low and len(ids) >= 2:
            return _call("compare_jobs", {"job_ids": ids[:3]})
        if ids and "eligib" in low:
            return _call("check_eligibility", {"job_id": ids[0]})
        if ids:
            return _call("get_job", {"job_id": ids[0]})
        return _call("search_jobs", {"query": text[:200], "limit": 5})
    outs = [json.loads(m["content"]) for m in after if m["role"] == "tool"]
    lines: list[str] = []
    for o in outs:
        if "saved" in o:
            lines.append(f"Saved: {json.dumps(o['saved'])}")
        for r in o.get("results") or o.get("jobs") or ([o] if "job_id" in o else []):
            v = r.get("eligibility") or r.get("verdict")
            lines.append(f"- {r.get('title')}, {r.get('company')} (job_id: {r['job_id']})" + (f": {v}" if v else ""))
        if "error" in o:
            lines.append(f"Tool error: {o['error']}")
    return LLMResult(content="\n".join(lines) or "No jobs found.")
