### P6 agent test conversations, profile p00 (n=10)

Grounding check passed in 3 of 10 conversations. Total tokens 42987 (agent + explain calls; cached calls count their original tokens).

| Conv | User message(s) | Tools | Grounding | Flags | Tokens | Stop |
|---|---|---|---|---|---|---|
| c01 | best internships for me in Bengaluru | search_jobs, check_eligibility, check_eligibility | FAIL | id 7723230003; id 8229735 | 6273 | - |
| c02 | am I eligible for JNJ TLDP | search_jobs, get_job, check_eligibility | PASS |  | 6089 | - |
| c03 | compare Elip and Rubrik | search_jobs, compare_jobs, check_eligibility | FAIL | cgpa 7.96 | 6151 | - |
| c04 | remember that I prefer Bengaluru / now show me software jobs for freshers | remember, search_jobs | FAIL | id 8130876; id 4731054005 | 5000 | - |
| c05 | any remote data science or machine learning roles for a 2027 graduate? | search_jobs, get_job, check_eligibility | FAIL | id 6185008004; id 5703396 | 6829 | - |
| c06 | what does the Elip software engineer job pay, and what is the deadline? | search_jobs, get_job | PASS |  | 4111 | - |
| c07 | do any of my matches look risky or ask for a fee? |  | FAIL | other 400 | 0 | llm_error: BadRequestError: Error code: 400 - {'error': {'message': 'Tool call validation failed: tool call validation failed: parameters for tool search_jobs did not match schema: errors: |
| c08 | find my top match and explain why it fits me | search_jobs, explain_match, check_eligibility | FAIL | id 8229735; cgpa 2027; cgpa 7.96 | 5931 | - |
| c09 | full-time roles paying at least 50000 a month |  | FAIL | other 400 | 0 | llm_error: BadRequestError: Error code: 400 - {'error': {'message': "Tool call validation failed: tool call validation failed: parameters for tool search_jobs did not match schema: errors: |
| c10 | am I eligible for the Google SDE role? | search_jobs | PASS |  | 2603 | - |
