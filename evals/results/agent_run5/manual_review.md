### Manual review, agent_run5 (profile p00, prompt agent_v4): targeted re-run of c05, c09, c11, c13

Reviewer: Claude. I read every answer against that turn's raw tool outputs and checked the database where noted.
Ravi's own reading is still pending. This is not a hand label by Ravi.

Run: 2026-11-20, Groq openai/gpt-oss-120b, 4 conversations (4 turns), 15,764 tokens. Only the four conversations
judged wrong or not answered in run 4 were re-run; the other 11 keep their run-4 results (`agent_run4/`).

Changes since run 4:
- Search results carry pay, apply_deadline, work_mode and employment_type.
- Expired jobs are left out. The 4 JNJ pieces #1302–#1305 have a 2026-11-19 deadline, so searchable in-scope jobs
  go from 109 to 105.
- Grounding flags "pay / deadline not listed" claims unless that job's field is null.
- Prompt agent_v4.

Memory was empty for these runs (`--only` clears it). In run 4, c09–c13 ran with "preferred_locations: Bengaluru"
left over from c04.

Automatic grounding check: **4 of 4** pass. Tools-only gives 3 of 4: c09 repeats the user's own ₹50,000.

**Manual result: 2 correct, 1 minor, 1 wrong.**
In run 4, all four of these were wrong or not answered.

| Conv | Run 4 | Run 5 | What is right or wrong |
|---|---|---|---|
| c05 | wrong | **correct** | 5 remote jobs. Every rule list matches its own job. Pay is copied in its own currency (USD 230,000–450,000/year for Harvey, GBP 65,000–100,000 for Lightdash), not converted. Ghost signals are named. "All listed jobs are remote" is now true: every listed job has work_mode remote. Notes: it leaves out the two name matches (527 hybrid, 693 Coinbase "Remote - India" INELIGIBLE) without saying so. Lightdash "Analytics Engineering Advocate - Europe" is in scope although its title names Europe; that is a data issue, not the agent's. |
| c09 | wrong | **minor** | "No full-time roles meeting the ₹50,000 per-month pay requirement are currently in the database." The search (filter employment_type full_time, pay ≥ ₹50,000) returned 0, and the database agrees: with TLDP expired, no searchable full-time job meets it. But 7 searchable jobs pay ≥ ₹50,000/month with employment type not stated (for example Coinbase Machine Learning Engineer, INR 44,08,400/year; the Harvey roles, USD), and the answer does not mention them. It also searched with a query the user did not give ("software engineer"). |
| c11 | not answered | **correct** | It calls search_jobs. Elip 1295 CHECK, "no fee information is listed", no scam signals (scam_rules_fired empty, so the money rule did not fire). |
| c13 | wrong | **wrong** | The facts are now right: all 5 results have pay null, and it says so with "(pay = null)". The automatic check accepts this correctly. The conclusion is not: "there is no information about which job pays the most" holds only for its 5 "software engineer intern" results. The database has 9 searchable jobs with pay, the highest in INR being Coinbase 693 (₹44,08,400/year) and Elip 1295 (₹75,000/month). There is still no way to sort by pay. |

### What run 5 fixed
- No more "pay not listed" claims for jobs whose details were not fetched: search results now carry pay and
  deadline, and the check flags such claims. Offline re-scoring of runs 3 and 4 with the new check flags c13 (both
  runs) and c09 (run 4, now for pay and deadline rather than the false "pay" rule flag), and no other turn.
- "All remote" is now backed by each job's work_mode (c05).
- c11 calls a tool.
- Expired JNJ jobs no longer appear.

### Still open
- Conclusions about the whole database drawn from one 5-result search (c09, c13). This needs a sort-by-pay option or
  an "unknown type" note; the grounding check cannot see it.
- Most board jobs have no extracted employment_type (null), so an employment-type filter drops them silently (c09).
- Region named in a title ("- Europe") is not caught by the India rule (Lightdash 987).
