"""Hand-written tool-calling agent loop (spec 14.3). No agent framework.

run_turn: system prompt (profile, memory, session summary) + last 10 messages + user message, then up to
MAX_STEPS rounds of chat_tools -> execute tools -> append results. After the answer, the grounding check
(app.agent.validate) verifies every job id and number against this turn's tool outputs.
Tokens are counted per turn. Provider limits (daily quota, rate limit after retries, budget) stop the turn
cleanly with a stop reason instead of raising.
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import logging
import uuid
from typing import Any

import openai
from sqlalchemy.orm import Session

from app.agent import memory as mem
from app.agent.tools import ToolContext, ToolResult, effective_profile, execute_tool, load_memory, tool_schemas
from app.agent.validate import check_answer
from app.config import get_settings
from app.extract.prompting import build_messages
from app.llm.client import BudgetExceeded, DailyLimitReached
from app.logs import log_event
from app.schemas import Profile

log = logging.getLogger("joblens.agent")

LIMIT_ERRORS = (DailyLimitReached, BudgetExceeded, openai.RateLimitError)


def profile_summary(p: Profile) -> str:
    """Compact, anonymous profile line for the system prompt (no name or contact details)."""
    parts = [f"{p.degree or 'degree unknown'}" + (f" in {p.specialization}" if p.specialization else ""),
             f"graduating {p.graduation_year}" if p.graduation_year else "graduation year unknown",
             f"CGPA {p.cgpa}" if p.cgpa is not None else "CGPA unknown",
             f"{p.experience_years:g} years experience" if p.experience_years is not None else "",
             "skills: " + ", ".join(s.name for s in p.skills[:20]),
             f"preferred locations: {', '.join(p.preferred_locations)}" if p.preferred_locations else "",
             f"target roles: {', '.join(p.target_roles)}" if p.target_roles else "",
             f"minimum monthly pay INR {p.min_monthly_pay_inr}" if p.min_monthly_pay_inr else ""]
    return "; ".join(x for x in parts if x)


def _run_tool(name: str, args: dict[str, Any], ctx: ToolContext, timeout_s: float) -> ToolResult:
    # The DB session is not thread-safe, so tools run inline; the timeout is enforced on the elapsed time.
    res = execute_tool(name, args, ctx)
    if res.ms > timeout_s * 1000:
        res.output = {"error": f"tool timed out after {timeout_s:g}s"}
        res.error = "timeout"
    return res


def run_turn(s: Session, profile_id: uuid.UUID, profile: Profile, message: str, session_id: uuid.UUID | None = None,
             llm: Any = None, explain_llm: Any = None) -> dict[str, Any]:
    from app.llm.client import get_llm

    cfg = get_settings().cfg
    a = cfg.agent
    llm = llm or get_llm("agent")
    cs = mem.get_or_create_session(s, profile_id, session_id)
    memory = load_memory(s, profile_id)
    ctx = ToolContext(session=s, profile_id=profile_id, profile=effective_profile(profile, memory), memory=memory,
                      explain_llm=explain_llm)
    mem_items = "; ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in memory.items()) or "none"
    system = build_messages(cfg.prompts.agent, profile_summary=profile_summary(ctx.profile), memory_items=mem_items,
                            session_summary=cs.summary or "none")
    messages: list[dict[str, Any]] = [*system, *mem.last_messages(s, cs.id, a.history_messages),
                                      {"role": "user", "content": message}]
    mem.save_message(s, cs.id, "user", {"text": message})
    schemas = tool_schemas()
    trace: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []
    answer, stop = None, None
    llm_calls = 0
    for step in range(a.max_steps):
        try:
            res = llm.chat_tools(messages, schemas, temperature=a.temperature, prompt_version=cfg.prompts.agent)
        except LIMIT_ERRORS as e:
            stop = f"llm_limit: {type(e).__name__}: {str(e)[:160]}"
            break
        except (openai.APIError, cf.TimeoutError) as e:
            stop = f"llm_error: {type(e).__name__}: {str(e)[:160]}"
            break
        llm_calls += 1
        ctx.tokens["in"] += res.tokens_in
        ctx.tokens["out"] += res.tokens_out
        if not res.tool_calls:
            answer = res.content or ""
            break
        messages.append(res.assistant_message())
        for call in res.tool_calls:
            tr = _run_tool(call.name, call.args(), ctx, a.tool_timeout_s)
            content = tr.content()
            outputs.append(tr.output)
            trace.append({"step": step, "tool": call.name, "args": tr.args, "ms": round(tr.ms, 1),
                          "result_chars": len(content), "error": tr.error, "output": tr.output})
            log_event(log, "agent_tool", tool=call.name, args=tr.args, ms=round(tr.ms, 1), chars=len(content),
                      error=tr.error)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": content})
    else:
        stop = f"max_steps ({a.max_steps}) reached without a final answer"
    if answer is None:
        answer = ("I could not finish this request: " + (stop or "unknown stop") +
                  ". Nothing below is a recommendation; please try again later.")
    grounding = check_answer(answer, outputs)
    mem.save_message(s, cs.id, "assistant", {"text": answer, "tool_trace": [{k: v for k, v in t.items() if k != "output"}
                                                                          for t in trace],
                                             "grounding": grounding.to_dict(), "stop": stop})
    summarized = False
    if stop is None:
        summarized = mem.maybe_summarize(s, cs, llm, a.summary_every, ctx.tokens)
    log_event(log, "agent_turn", steps=llm_calls, tools=len(trace), tokens_in=ctx.tokens["in"],
              tokens_out=ctx.tokens["out"], grounded=grounding.ok, stop=stop)
    return {"session_id": str(cs.id), "answer": answer, "tool_trace": trace, "grounding": grounding.to_dict(),
            "tokens": {"in": ctx.tokens["in"], "out": ctx.tokens["out"], "total": ctx.tokens["in"] + ctx.tokens["out"]},
            "llm_calls": llm_calls, "stop": stop, "summary_refreshed": summarized, "model": llm.model}
