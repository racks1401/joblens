### Manual review, agent_run3 (profile p00)

Reviewer: Claude (reading every answer against that turn's raw tool outputs; database checked where noted).
Ravi's own reading is still pending. This is not a hand label by Ravi.

Automatic grounding check: 14 of 15 conversations pass (tools-only rule: 14 of 15). The one failure is c15 turn 2,
which did not run: the agent budget (180K tokens/day) was reached, and the stop message itself has the two budget numbers.

**Manual result: 6 correct, 3 with minor issues, 5 with real mistakes, 1 incomplete (c15: turn 2 not run).**

| Conv | Manual verdict | What is wrong |
|---|---|---|
| c01 | **wrong** | Only job 350 was checked (check_eligibility), but the answer gives "Failed: degree – the posting allows BTECH" for all 5 jobs. For 1149 (Razorpay) the stored degrees are null, so that reason is false; for 1281 the job allows BTECH, BE, MTECH, MSC. It also says "no red-flag indicators", but search results have no posting_red_flags field, and job 360 has a ghost signal (age_60). The verdicts (INELIGIBLE) are correct. |
| c02 | **wrong** | Lists TLDP (1303) as CHECK, which is correct, but never calls check_eligibility, so it does not say which rules need checking (batch year, Hyderabad). It says "7 jobs checked" when the tool returned 6, and claims "no red-flag indicators" from search output that has no red-flag field (660 and 1280 have ghost signals). Run 2 answered this question correctly. |
| c03 | correct | Elip CHECK with exactly its needs_checking list; Rubrik INELIGIBLE with exactly its failed list (degree, cgpa). It is terse, with no pay or duration, but nothing in it is wrong. |
| c04 | **wrong** | Turn 1 (remember): the preference is saved correctly, but the agent also ran an unrequested search, and gives "Failed: degree, specialization, graduation_year, cgpa" for 522, 1278 and 1286, which were never checked. In turn 2, the same invented failed list appears for 1270, 341, 1149 and 360; only 350 was checked. It also says posting_red_flags are empty for all five, but that field was fetched only for 350. |
| c05 | **wrong** | The needs_checking list of 1091 (the only job checked) is copied onto 1160, 1159, 1158 and 1095. Invented failed lists are given for 527 and 693. The verdicts themselves are correct. |
| c06 | correct | ₹75,000 per month, deadline not stated, CHECK with the right list. |
| c07 | minor | It counts the jobs it checked (5) and says scam_rules_fired is empty for all, which is correct, so no fee rule fired. But it also says "no red-flag signals", while 1125, 1108 and 1278 carry ghost signals (age_60, age_120). And "your matches" was read as one search for software engineer interns in Bengaluru. |
| c08 | minor | Top match 539 CHECK with the right list; no scam signals is correct. The explanation ran (it was not skipped, now that budgets are per purpose), but the agent does not say why the job fits. The explanation LLM's own summary ("meets all the conditions") contradicts CHECK; the agent did not repeat it. |
| c09 | correct | TLDP 1303 CHECK (graduation_year, location), ₹15,65,000/year ≈ ₹1,30,417/month from the tool. The USD job is gone (seniority and time-zone fixes). |
| c10 | correct | Says there is no Google SDE posting. The wording "6 jobs compared" is odd, but there are no false facts. |
| c11 | correct | Elip CHECK; scam_rules_fired empty, so no fee rule fired. It does not claim more than the search output shows. |
| c12 | minor | Stripe Software Engineer, Intern (1108) is INELIGIBLE (degree), which is correct, and it answers "definitely eligible?" with no. But there are 3 Stripe jobs (1270 INELIGIBLE, 205 CHECK), and it picked one without saying so. |
| c13 | **wrong** | It says "posting does not state it" for pay on all 5 jobs, but only 539 and 1125 were fetched with get_job. For 1108, 1278 and 350 the claim is not from this turn's tools; the database shows it is true. It does say the highest-paying job cannot be identified, but only across 5 search results, not the database. |
| c14 | correct | Says there are no Zerodha jobs in the database. |
| c15 | incomplete | Turn 1 saved Hyderabad correctly. Turn 2 was stopped cleanly by the agent's daily budget (180,336 of 180,000 tokens; Groq's own limit was not reached). One turn is left, and it can run after 00:00 UTC. |

**Main remaining failure (c01, c04, c05):** when the agent checks one job's rules, it copies that job's failed or
needs_checking list onto jobs it never checked. The grounding check cannot see this, because it checks ids, numbers
and verdict words, not per-job rule names.

Proposed fix, not applied:
1. search_jobs returns needs_checking and failed for every result (they are already computed for ranking).
2. The grounding check compares the rule names stated next to each job id with that job's tool output.
3. Statements about pay or red flags for a job count only if that job's fields were in this turn's output.
