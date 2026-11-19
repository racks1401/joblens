### Agent test conversations, profile p00 (agent_run3, n=15 run)

Grounding check (job ids, numbers and verdicts vs this turn's tool outputs; real profile values and numbers from the user's own message accepted): **14 of 15** passed. Strict, tool outputs only: 14 of 15. Total tokens 84521 (agent + summary + explain calls).

| Conv | User message(s) | Tools | Grounding | Tools-only | Flags | Tokens | Stop |
|---|---|---|---|---|---|---|---|
| c01 | best internships for me in Bengaluru | search_jobs, check_eligibility | PASS | PASS | - | 5385 | - |
| c02 | am I eligible for JNJ TLDP | search_jobs | PASS | PASS | - | 3164 | - |
| c03 | compare Elip and Rubrik | search_jobs, compare_jobs | PASS | PASS | - | 4937 | - |
| c04 | remember that I prefer Bengaluru / now show me software jobs for freshers | remember, search_jobs, check_eligibility, search_jobs, get_job, check_eligibility | PASS | PASS | - | 15360 | - |
| c05 | any remote data science or machine learning roles for a 2027 graduate? | search_jobs, check_eligibility | PASS | PASS | - | 5962 | - |
| c06 | what does the Elip software engineer job pay, and what is the deadline? | search_jobs, get_job, check_eligibility | PASS | PASS | - | 6862 | - |
| c07 | do any of my matches look risky or ask for a fee? | search_jobs, search_jobs | PASS | PASS | - | 3384 | - |
| c08 | find my top match and explain why it fits me | search_jobs, check_eligibility, explain_match | PASS | PASS | - | 7531 | - |
| c09 | full-time roles paying at least 50000 a month | search_jobs, search_jobs, get_job, check_eligibility | PASS | PASS | - | 6388 | - |
| c10 | am I eligible for the Google SDE role? | search_jobs | PASS | PASS | - | 3021 | - |
| c11 | does Elip charge any fee | search_jobs, check_eligibility | PASS | PASS | - | 4788 | - |
| c12 | am I definitely eligible for the Stripe role | search_jobs, check_eligibility | PASS | PASS | - | 4957 | - |
| c13 | which job pays the most | search_jobs, search_jobs, get_job, get_job | PASS | PASS | - | 7503 | - |
| c14 | tell me about jobs at Zerodha | search_jobs | PASS | PASS | - | 2862 | - |
| c15 | remember that I prefer Hyderabad / show jobs in my preferred city | remember | FAIL | FAIL | other 180336; other 180000 | 2417 | llm_limit: BudgetExceeded: Daily token budget for 'agent' reached (180336 of 180 |
