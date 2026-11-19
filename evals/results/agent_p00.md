### P6 agent test conversations, profile p00 (n=10)

Grounding check passed in 9 of 10 conversations; 1 more failed only because the answer repeated the user's own profile values (CGPA, batch year), which are not in that turn's tool outputs. Total tokens 47749 (agent + explain calls; cached calls count their original tokens).

| Conv | User message(s) | Tools | Grounding | Flags | Tokens | Stop |
|---|---|---|---|---|---|---|
| c01 | best internships for me in Bengaluru | search_jobs, check_eligibility | PASS |  | 4485 | - |
| c02 | am I eligible for JNJ TLDP | search_jobs, get_job, check_eligibility | PASS |  | 6057 | - |
| c03 | compare Elip and Rubrik | search_jobs, compare_jobs | PASS |  | 4246 | - |
| c04 | remember that I prefer Bengaluru / now show me software jobs for freshers | remember, search_jobs | PASS |  | 4822 | - |
| c05 | any remote data science or machine learning roles for a 2027 graduate? | search_jobs, check_eligibility, check_eligibility | FAIL | other 2027 (profile value) | 6750 | - |
| c06 | what does the Elip software engineer job pay, and what is the deadline? | search_jobs, get_job | PASS |  | 4118 | - |
| c07 | do any of my matches look risky or ask for a fee? | search_jobs, search_jobs | PASS |  | 2697 | - |
| c08 | find my top match and explain why it fits me | search_jobs, check_eligibility, explain_match | PASS |  | 6058 | - |
| c09 | full-time roles paying at least 50000 a month | search_jobs, search_jobs, get_job, check_eligibility | PASS |  | 5921 | - |
| c10 | am I eligible for the Google SDE role? | search_jobs | PASS |  | 2595 | - |
