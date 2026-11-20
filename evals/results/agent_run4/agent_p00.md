### Agent test conversations, profile p00 (agent_run4, n=15 run)

Grounding check (job ids, numbers and verdicts vs this turn's tool outputs; real profile values and numbers from the user's own message accepted): **14 of 15** passed. Strict, tool outputs only: 12 of 15. Total tokens 68835 (agent + summary + explain calls).

| Conv | User message(s) | Tools | Grounding | Tools-only | Flags | Tokens | Stop |
|---|---|---|---|---|---|---|---|
| c01 | best internships for me in Bengaluru | search_jobs | PASS | FAIL | accepted: profile 2027, profile 7.96 | 3999 | - |
| c02 | am I eligible for JNJ TLDP | search_jobs, check_eligibility | PASS | PASS | - | 5689 | - |
| c03 | compare Elip and Rubrik | search_jobs, compare_jobs | PASS | PASS | - | 5789 | - |
| c04 | remember that I prefer Bengaluru / now show me software jobs for freshers | remember, search_jobs | PASS | PASS | - | 6645 | - |
| c05 | any remote data science or machine learning roles for a 2027 graduate? | search_jobs | PASS | PASS | - | 4253 | - |
| c06 | what does the Elip software engineer job pay, and what is the deadline? | search_jobs, get_job | PASS | PASS | - | 5512 | - |
| c07 | do any of my matches look risky or ask for a fee? | search_jobs, search_jobs | PASS | PASS | - | 3888 | - |
| c08 | find my top match and explain why it fits me | search_jobs, explain_match | PASS | FAIL | accepted: profile 2027, profile 7.96 | 5677 | - |
| c09 | full-time roles paying at least 50000 a month | search_jobs, search_jobs | FAIL | FAIL | rules 1303: pay not in its lists ['graduation_year', 'location'] | 3196 | - |
| c10 | am I eligible for the Google SDE role? | search_jobs | PASS | PASS | - | 3449 | - |
| c11 | does Elip charge any fee |  | PASS | PASS | - | 1405 | - |
| c12 | am I definitely eligible for the Stripe role | search_jobs | PASS | PASS | - | 3450 | - |
| c13 | which job pays the most | search_jobs, search_jobs, get_job | PASS | PASS | - | 6133 | - |
| c14 | tell me about jobs at Zerodha | search_jobs | PASS | PASS | - | 3322 | - |
| c15 | remember that I prefer Hyderabad / show jobs in my preferred city | remember, search_jobs | PASS | PASS | - | 6428 | - |
