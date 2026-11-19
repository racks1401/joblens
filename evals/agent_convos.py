"""Run the 10 P6 test conversations for profile p00 and save full transcripts.

    python -m evals.agent_convos [--only c02,c03]

Uses the anonymized p00 profile from evals/data/profiles.jsonl, attached to the saved DB profile row (needed for
memory and sessions). p00's long-term memory is cleared before and after the run so results are reproducible.
Stops cleanly when an LLM limit is hit and records which conversations did not run.
Writes evals/results/agent_transcripts/cNN.{json,md} and evals/results/agent_p00.{json,md}.
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
OUTDIR = RESULTS / "agent_transcripts"

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
]


def p00() -> Profile:
    for line in (ROOT / "evals" / "data" / "profiles.jsonl").read_text(encoding="utf-8").splitlines():
        d = json.loads(line)
        if d["profile_id"] == "p00":
            return Profile.model_validate(d["profile"])
    raise SystemExit("p00 not in evals/data/profiles.jsonl")


def md(cid: str, turns: list[dict[str, Any]]) -> str:
    out = [f"# Agent transcript {cid} (profile p00)\n"]
    for i, t in enumerate(turns, 1):
        r = t["result"]
        out += [f"## Turn {i}\n", f"**User:** {t['user']}\n", "**Tool calls:**\n"]
        for tr in r["tool_trace"]:
            out.append(f"- step {tr['step']}: `{tr['tool']}({json.dumps(tr['args'], ensure_ascii=False)})` "
                       f"{tr['ms']} ms, {tr['result_chars']} chars" + (f", error: {tr['error']}" if tr["error"] else ""))
            out.append(f"  - output: `{json.dumps(tr['output'], ensure_ascii=False)[:1500]}`")
        g = r["grounding"]
        out += ["", f"**Assistant:**\n\n{r['answer']}\n",
                f"**Grounding:** {'PASS' if g['ok'] else 'FAIL'} - job ids checked {g['job_ids_checked']}, "
                f"numbers checked {g['numbers_checked']}, unsupported ids {g['unsupported_job_ids']}, "
                f"unsupported numbers {g['unsupported_numbers']}, named without id {g['jobs_named_without_id']}, "
                f"small numbers skipped {g['skipped_small_numbers']}\n",
                f"**Tokens:** {r['tokens']} in {r['llm_calls']} agent LLM calls (model {r['model']}); "
                f"stop: {r['stop']}; {t['seconds']} s\n"]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    only = set(filter(None, args.only.split(",")))
    profile = p00()
    OUTDIR.mkdir(parents=True, exist_ok=True)
    summary: list[dict[str, Any]] = []
    limit_hit = None
    with session_factory()() as s:
        pid: uuid.UUID = s.execute(select(ProfileRow.id).order_by(ProfileRow.created_at)).scalars().first()
        delete_memory(s, pid)
        for cid, msgs in CONVOS:
            if only and cid not in only:
                continue
            if limit_hit:
                summary.append({"id": cid, "ran": False, "reason": f"not run: {limit_hit}"})
                continue
            turns: list[dict[str, Any]] = []
            sid = None
            for m in msgs:
                t0 = time.perf_counter()
                r = run_turn(s, pid, profile, m, session_id=sid)
                sid = uuid.UUID(r["session_id"])
                turns.append({"user": m, "result": r, "seconds": round(time.perf_counter() - t0, 1)})
                print(cid, m[:50], "| grounded:", r["grounding"]["ok"], "| tokens:", r["tokens"], "| stop:", r["stop"],
                      flush=True)
                if r["stop"] and r["stop"].startswith("llm_limit"):
                    limit_hit = r["stop"]
                    break
            mem_after = list_memory(s, pid)
            rec = {"id": cid, "profile": "p00", "turns": turns, "memory_after": mem_after}
            (OUTDIR / f"{cid}.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
            (OUTDIR / f"{cid}.md").write_text(md(cid, turns) + f"\nMemory after: `{json.dumps(mem_after)}`\n",
                                              encoding="utf-8")
            summary.append({"id": cid, "ran": True, "messages": msgs,
                            "grounded": all(t["result"]["grounding"]["ok"] for t in turns),
                            "turns": [{"grounding": t["result"]["grounding"], "stop": t["result"]["stop"],
                                       "tools": [x["tool"] for x in t["result"]["tool_trace"]],
                                       "tokens": t["result"]["tokens"]} for t in turns]})
        delete_memory(s, pid)
    tot = sum(t["tokens"]["total"] for c in summary if c.get("ran") for t in c["turns"])
    (RESULTS / "agent_p00.json").write_text(json.dumps({"conversations": summary, "total_tokens": tot}, indent=1,
                                                       default=str), encoding="utf-8")
    rows = ["| Conv | User message(s) | Tools | Grounding | Flags | Tokens | Stop |", "|---|---|---|---|---|---|---|"]
    for c in summary:
        if not c.get("ran"):
            rows.append(f"| {c['id']} | | | not run | | | {c['reason']} |")
            continue
        flags = []
        for t in c["turns"]:
            g = t["grounding"]
            flags += [f"id {x}" for x in g["unsupported_job_ids"]] + [f"{u['kind']} {u['value']}" + (" (profile value)" if u.get("in_profile") else "")
                                                                    for u in g["unsupported_numbers"]]
            flags += [f"no id: {j['job_id']}" for j in g["jobs_named_without_id"]]
        rows.append(f"| {c['id']} | {' / '.join(c['messages'])} | {', '.join(x for t in c['turns'] for x in t['tools'])} | "
                    f"{'PASS' if c['grounded'] else 'FAIL'} | {'; '.join(flags)} | "
                    f"{sum(t['tokens']['total'] for t in c['turns'])} | {', '.join(str(t['stop']) for t in c['turns'] if t['stop']) or '-'} |")
    n_ran = sum(1 for c in summary if c.get("ran"))
    n_ok = sum(1 for c in summary if c.get("ran") and c["grounded"])
    n_prof = sum(1 for c in summary if c.get("ran") and not c["grounded"]
                 and all(t["grounding"]["ok"] or t["grounding"].get("only_profile_values_flagged") for t in c["turns"]))
    (RESULTS / "agent_p00.md").write_text(
        f"### P6 agent test conversations, profile p00 (n={n_ran})\n\nGrounding check passed in {n_ok} of {n_ran} "
        f"conversations; {n_prof} more failed only because the answer repeated the user's own profile values "
        f"(CGPA, batch year), which are not in that turn's tool outputs. Total tokens {tot} (agent + explain calls; cached calls count their original tokens).\n\n"
        + "\n".join(rows) + "\n", encoding="utf-8")
    print(f"grounded {n_ok}/{n_ran}, total tokens {tot}")


if __name__ == "__main__":
    main()
