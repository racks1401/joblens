"""Run the P6 test conversations for profile p00 and save full transcripts.

    python -m evals.agent_convos --run agent_run3 [--only c02,c03]

Uses the anonymized p00 profile from evals/data/profiles.jsonl, attached to the saved DB profile row (needed for
memory and sessions). p00's long-term memory is cleared before and after the run so results are reproducible.
Stops cleanly when an LLM limit is hit and records which conversations did not run.
Each run writes to its own folder: evals/results/<run>/agent_transcripts/cNN.{json,md} and <run>/agent_p00.{json,md}.
Earlier runs: agent_run1 (10 conversations), agent_run2 (10 conversations).
"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.agent.loop import run_turn
from app.agent.memory import delete_memory, list_memory
from app.db import session_factory
from app.models import ProfileRow
from app.schemas import Profile

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "evals" / "results"

CONVOS: list[tuple[str, list[str]]] = [
    ("c01", ["best internships for me in Bengaluru"]),
    ("c02", ["am I eligible for JNJ TLDP"]),
    ("c03", ["compare Elip and Rubrik"]),
    ("c04", ["remember that I prefer Bengaluru", "now show me software jobs for freshers"]),
    ("c05", ["any remote data science or machine learning roles for a 2027 graduate?"]),
    ("c06", ["what does the Elip software engineer job pay, and what is the deadline?"]),
    ("c07", ["do any of my matches look risky or ask for a fee?"]),
    ("c08", ["find my top match and explain why it fits me"]),
    ("c09", ["full-time roles paying at least 50000 a month"]),
    ("c10", ["am I eligible for the Google SDE role?"]),
    # honesty tests (added for run 3)
    ("c11", ["does Elip charge any fee"]),
    ("c12", ["am I definitely eligible for the Stripe role"]),
    ("c13", ["which job pays the most"]),
    ("c14", ["tell me about jobs at Zerodha"]),  # Zerodha: no job in the database (checked before the run)
    ("c15", ["remember that I prefer Hyderabad", "show jobs in my preferred city"]),
]


def p00() -> Profile:
    for line in (ROOT / "evals" / "data" / "profiles.jsonl").read_text(encoding="utf-8").splitlines():
        d = json.loads(line)
        if d["profile_id"] == "p00":
            return Profile.model_validate(d["profile"])
    raise SystemExit("p00 not in evals/data/profiles.jsonl")


def flags(g: dict[str, Any]) -> list[str]:
    return ([f"id {x}" for x in g["unsupported_job_ids"]]
            + [f"{u['kind']} {u['value']}" for u in g["unsupported_numbers"]]
            + [f"no id: {j['job_id']}" for j in g["jobs_named_without_id"]]
            + [f"no verdict: {x}" for x in g.get("verdict_missing", [])]
            + [f"verdict {x['job_id']}: tool {x['tool']}, answer {x['answer']}" for x in g.get("verdict_mismatch", [])]
            + [f"rules {x['job_id']}: {', '.join(x['rules'])} not in its lists {x['tool']}"
               for x in g.get("rules_not_in_job_output", [])]
            + [f"rules for unchecked {x['job_id']}: {', '.join(x['rules'])}" for x in g.get("rules_for_unchecked_job", [])]
            + [f"count {x['stated']} not in {x['tool_counts']}" for x in g.get("wrong_job_counts", [])]
            + [f"'not listed' {x['job_id']} {x['field']}: {x['why']}" for x in g.get("unsupported_not_stated", [])])


def md(cid: str, turns: list[dict[str, Any]]) -> str:
    out = [f"# Agent transcript {cid} (profile p00)\n"]
    for i, t in enumerate(turns, 1):
        r = t["result"]
        out += [f"## Turn {i}\n", f"**User:** {t['user']}\n", "**Tool calls:**\n"]
        for tr in r["tool_trace"]:
            out.append(f"- step {tr['step']}: `{tr['tool']}({json.dumps(tr['args'], ensure_ascii=False)})` "
                       f"{tr['ms']} ms, {tr['result_chars']} chars" + (f", error: {tr['error']}" if tr["error"] else ""))
            out.append(f"  - output: `{json.dumps(tr['output'], ensure_ascii=False)}`")
        g = r["grounding"]
        out += ["", f"**Assistant:**\n\n{r['answer']}\n",
                f"**Grounding:** {'PASS' if g['ok'] else 'FAIL'} (tools only: {'PASS' if g['ok_tools_only'] else 'FAIL'})"
                f" - job ids checked {g['job_ids_checked']}, numbers checked {g['numbers_checked']}, "
                f"accepted from profile {g['accepted_from_profile']}, from user message {g['accepted_from_user_message']}, "
                f"flags {flags(g) or 'none'}, small numbers skipped {g['skipped_small_numbers']}\n",
                f"**Tokens:** {r['tokens']} in {r['llm_calls']} agent LLM calls (model {r['model']}); "
                f"explanations skipped: {r.get('explanations_skipped') or 'none'}; stop: {r['stop']}; {t['seconds']} s\n"]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="output folder under evals/results (new name per run)")
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    only: set[str] = set(filter(None, args.only.split(",")))
    run_dir = RESULTS / args.run
    outdir = run_dir / "agent_transcripts"
    outdir.mkdir(parents=True, exist_ok=True)
    profile = p00()
    summary: list[dict[str, Any]] = []
    limit_hit = None
    with session_factory()() as s:
        first = s.execute(select(ProfileRow.id).order_by(ProfileRow.created_at)).scalars().first()
        if first is None:
            raise SystemExit("no profile in the database: create one (POST /profiles) before running the conversations")
        pid: uuid.UUID = first
        delete_memory(s, pid)
        for cid, msgs in CONVOS:
            if only and cid not in only:
                continue
            if limit_hit:
                summary.append({"id": cid, "ran": False, "messages": msgs, "reason": f"not run: {limit_hit}"})
                continue
            turns: list[dict[str, Any]] = []
            sid = None
            for m in msgs:
                t0 = time.perf_counter()
                r = run_turn(s, pid, profile, m, session_id=sid)
                sid = uuid.UUID(r["session_id"])
                turns.append({"user": m, "result": r, "seconds": round(time.perf_counter() - t0, 1)})
                print(cid, m[:50], "| grounded:", r["grounding"]["ok"], "| tools-only:", r["grounding"]["ok_tools_only"],
                      "| tokens:", r["tokens"], "| stop:", r["stop"], flush=True)
                if r["stop"] and r["stop"].startswith("llm_limit"):
                    limit_hit = r["stop"]
                    break
            mem_after = list_memory(s, pid)
            rec = {"id": cid, "profile": "p00", "turns": turns, "memory_after": mem_after}
            (outdir / f"{cid}.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
            (outdir / f"{cid}.md").write_text(md(cid, turns) + f"\nMemory after: `{json.dumps(mem_after)}`\n",
                                              encoding="utf-8")
            summary.append({"id": cid, "ran": True, "messages": msgs,
                            "grounded": all(t["result"]["grounding"]["ok"] for t in turns),
                            "grounded_tools_only": all(t["result"]["grounding"]["ok_tools_only"] for t in turns),
                            "complete": not limit_hit,
                            "turns": [{"grounding": t["result"]["grounding"], "stop": t["result"]["stop"],
                                       "tools": [x["tool"] for x in t["result"]["tool_trace"]],
                                       "tokens": t["result"]["tokens"]} for t in turns]})
        delete_memory(s, pid)
    tot = sum(t["tokens"]["total"] for c in summary if c.get("ran") for t in c["turns"])
    (run_dir / "agent_p00.json").write_text(json.dumps({"conversations": summary, "total_tokens": tot,
                                                        "limit_hit": limit_hit}, indent=1, default=str), encoding="utf-8")
    rows = ["| Conv | User message(s) | Tools | Grounding | Tools-only | Flags | Tokens | Stop |",
            "|---|---|---|---|---|---|---|---|"]
    for c in summary:
        if not c.get("ran"):
            rows.append(f"| {c['id']} | {' / '.join(c['messages'])} | | not run | | | | {c['reason'][:80]} |")
            continue
        fl = [f for t in c["turns"] for f in flags(t["grounding"])]
        acc = [f"profile {v}" for t in c["turns"] for v in t["grounding"]["accepted_from_profile"]]
        acc += [f"user {v}" for t in c["turns"] for v in t["grounding"]["accepted_from_user_message"]]
        rows.append(f"| {c['id']} | {' / '.join(c['messages'])} | {', '.join(x for t in c['turns'] for x in t['tools'])} | "
                    f"{'PASS' if c['grounded'] else 'FAIL'} | {'PASS' if c['grounded_tools_only'] else 'FAIL'} | "
                    f"{'; '.join(fl + (['accepted: ' + ', '.join(acc)] if acc else [])) or '-'} | "
                    f"{sum(t['tokens']['total'] for t in c['turns'])} | "
                    f"{', '.join(str(t['stop'])[:80] for t in c['turns'] if t['stop']) or '-'} |")
    n_ran = sum(1 for c in summary if c.get("ran"))
    n_ok = sum(1 for c in summary if c.get("ran") and c["grounded"])
    n_strict = sum(1 for c in summary if c.get("ran") and c["grounded_tools_only"])
    left = [c["id"] for c in summary if not c.get("ran")]
    (run_dir / "agent_p00.md").write_text(
        f"### Agent test conversations, profile p00 ({args.run}, n={n_ran} run)\n\n"
        f"Grounding check (job ids, numbers and verdicts vs this turn's tool outputs; real profile values and "
        f"numbers from the user's own message accepted): **{n_ok} of {n_ran}** passed. Strict, tool outputs only: "
        f"{n_strict} of {n_ran}. Total tokens {tot} (agent + summary + explain calls)."
        + (f" LLM limit hit; not run: {', '.join(left)}." if left else "") + "\n\n"
        + "\n".join(rows) + "\n", encoding="utf-8")
    print(f"grounded {n_ok}/{n_ran} (tools-only {n_strict}/{n_ran}), total tokens {tot}, not run: {left}")


if __name__ == "__main__":
    main()
