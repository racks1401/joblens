### Agent test conversations, profile p00 (agent_run5, n=4 run)

Grounding check (job ids, numbers and verdicts vs this turn's tool outputs; real profile values and numbers from the user's own message accepted): **4 of 4** passed. Strict, tool outputs only: 3 of 4. Total tokens 15764 (agent + summary + explain calls).

| Conv | User message(s) | Tools | Grounding | Tools-only | Flags | Tokens | Stop |
|---|---|---|---|---|---|---|---|
| c05 | any remote data science or machine learning roles for a 2027 graduate? | search_jobs | PASS | PASS | - | 4796 | - |
| c09 | full-time roles paying at least 50000 a month | search_jobs, search_jobs | PASS | FAIL | accepted: user ₹50,000 | 3365 | - |
| c11 | does Elip charge any fee | search_jobs | PASS | PASS | - | 3805 | - |
| c13 | which job pays the most | search_jobs | PASS | PASS | - | 3798 | - |
