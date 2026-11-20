### Manual review, agent_run4 (profile p00, prompt agent_v3)

Reviewer: Claude. I read every answer against that turn's raw tool outputs and checked the database where noted.
Ravi's own reading is still pending. This is not a hand label by Ravi.

Run: 2026-11-20 01:22 UTC, Groq openai/gpt-oss-120b, 15 conversations (17 turns), 68,835 tokens.
All 15 conversations ran, including both turns of c15. Run 3 could not finish c15 turn 2.
No budget or Groq limit was hit.

Automatic grounding check: **14 of 15** conversations pass. The strict tools-only rule gives **12 of 15**:
c01 and c08 fail it only because they repeat the user's real CGPA and graduation year.
The one automatic failure is c09.

Report fix made after the run: the "Flags" column and the per-transcript "flags" line did not show the three new
grounding fields (rules_not_in_job_output, rules_for_unchecked_job, wrong_job_counts), so c09 showed "FAIL, flags none".
`flags()` in evals/agent_convos.py now includes them. The .md files were rebuilt from the saved .json files; no
conversation was re-run and no result changed.

**Manual result: 7 correct, 3 minor, 1 not answered, 4 wrong.**
Run 3 had 6 correct, 3 minor, 5 wrong and 1 incomplete.

| Conv | Run 3 | Run 4 | What is right or wrong |
|---|---|---|---|
| c01 | wrong | **minor** | Fixed: every job's failed and needs_checking lists match that job's search output exactly, and the age_60 ghost signal on 360 is named. Left: the closing line says "all listed postings are internships or early-career roles", but only 350 is an internship in the database (360, 1149, 341 and 1281 have no type; 1281 fails on experience), and the search did not filter on internship. |
| c02 | wrong | **minor** | Correct job (TLDP 1303, from name_matches) and correct verdict CHECK; no wrong count. But the whole answer is one line. check_eligibility returned needs_checking [graduation_year, location] and the prompt says to list it, but the answer does not. |
| c03 | correct | correct | Elip 1295 CHECK with its exact list; Rubrik 1125 INELIGIBLE (degree, cgpa) with the age_60 signal (posted 74 days ago). |
| c04 | wrong | **correct** | Turn 1 only saves Bengaluru, with no extra search. Turn 2: 5 jobs, all INELIGIBLE, matching the tool; no copied rule lists; age_60 named on 360. It does not list each job's failed rule, but it states nothing false. |
| c05 | wrong | **wrong** | Fixed: all 7 rule lists match their own jobs, the count "5 results + 2 name-matches, 7 jobs" is right, and ghost signals are named. New mistake: "All listed jobs are remote" is false for 527 HackerRank (location "Hybrid in Bangalore", work_mode hybrid in the database). It also calls name_matches an "exact title match", but they are word matches. |
| c06 | correct | correct | ₹75,000 per month, deadline "the posting does not state it" (apply_deadline null in get_job), CHECK, no scam signals. |
| c07 | minor | **correct** | Ghost signals are named per job (age_60 for 1125 and 1278, age_120 for 1108), and "no fee request" is based on empty scam_rules_fired. It no longer says "no red flags". "Your matches" is still read as one search. |
| c08 | minor | **wrong** | Top match 539 CHECK with its exact list, score 92, no scam signals; all correct. Wrong: "no specific skill gaps or matches were listed, meaning your listed skills ... are compatible with the role's requirements". Both skill lists are empty, which supports no conclusion, so the compatibility claim is made up. "The posting is considered safe" overstates an empty scam-rule list. The cached explanation itself ("meets all the conditions", verdict strong, with CHECK and no matched skills) is weak; that is an explain_v1 problem, not the agent's. |
| c09 | correct | **wrong** | "No pay or deadline listed" for TLDP 1303 is false. get_job was never called, and the job passed the ≥ ₹50,000/month filter, so it has pay. The database has ₹15,65,000/year (≈ ₹1,30,417/month) and apply_deadline 2026-11-19. The agent also invented the query "software engineer", which the user did not ask for. The automatic check flags this turn, but only because the word "pay" sits on the same line as "needs checking", so the catch is partly luck. |
| c10 | correct | correct | Says Google SDE is not in the database. The search returned no Google job; the one name match is Hevo "SDE". |
| c11 | correct | **not answered** | No tool was called. It says "I don't have any information about fees or charges from Elip". Nothing in it is false, but the search tool would have shown Elip's scam_rules_fired (empty, so no money rule fired), and run 3 answered from it. |
| c12 | minor | **correct** | Lists all 3 Stripe jobs (1108 and 1270 INELIGIBLE, 205 CHECK) and answers "not definitely eligible". The duplicate-company rule worked. |
| c13 | wrong | **wrong** | It still does not answer the question: it ranks only the 5 results of an invented "software engineer intern" search. It calls get_job 539 (pay null), then says pay was "not checked" for 539, which should say "the posting does not state it". It ends with "None of the postings listed a salary", which only 539's output supports. This is true in the database for all 5, but the agent could not know that for 4 of them. The automatic check does not see it. |
| c14 | correct | correct | Says there are no Zerodha jobs in the database. The search returned none. |
| c15 | incomplete | **minor** | Both turns ran. Turn 1 saves Hyderabad. Turn 2 lists 5 jobs with the right verdicts, and "no scam signals" is true (scam_rules_fired empty). But 1288 and 1138 carry age_120 ghost signals, which the prompt says to name. TLDP 1303 (Hyderabad, location field null) is not among the 5. |

### Fixed since run 3

- Rules copied from one job onto others (c01, c04, c05): gone. In all 17 turns, every rule list in an answer matches
  that job's own needs_checking or failed list.
- "No red flags" said while ghost signals were present: gone. c07 names each signal; c15 says only "no scam signals".
- Wrong job counts ("7 jobs checked" for 6) are gone: the c05 count is exactly right.
- Remember turns no longer trigger a search (c04, c15).
- Several jobs at one company: c12 lists all 3 Stripe jobs.

### Mistakes that remain after run 4

1. **Pay and deadline claims about jobs it never fetched (c09, c13).** The agent says "no pay listed" or "none listed
   a salary" from search results, which carry no pay field. The prompt rule ("say were not checked") is followed
   once (c13 per job) and broken in the same answer. The grounding check only catches it by luck (c09) or not at all
   (c13).
2. **Made-up summary sentences (c01, c05, c08).** Closing lines generalize beyond the data: "all are internships",
   "all are remote", "your skills are compatible". The check works on ids, numbers, verdicts and rule names, so it
   cannot see these.
3. **Too terse or no tool use (c02, c11).** c02 drops the needs_checking list it fetched. c11 calls no tool and gives
   no answer.
4. **Question scope (c07, c09, c13).** "My matches", "full-time roles" and "which pays the most" become one invented
   5-result search instead of a ranked or filtered list. There is no "sort by pay" tool, so c13 cannot be answered
   well with the current tools.
5. **Explanation quality (c08).** The explain_v1 output can say "meets all the conditions" for a CHECK job with no
   matched skills.

Other finding (data, not the agent): TLDP 1303's apply_deadline is 2026-11-19, which has passed, but it is still
active and in scope, and searches return it.

Possible next fixes, not applied: a pay field (min, currency, period or null) in search results so pay claims can be
grounded; a check that "not stated", "not listed" or "no pay" claims are made only for jobs whose fetched field is
null; a sort_by="pay" option in search_jobs; an expiry step that deactivates jobs whose apply_deadline has passed.
