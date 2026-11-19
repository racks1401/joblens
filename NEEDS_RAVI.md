# What only you can do (JobLens)

Status: P0–P3 built; P4 harness built (E1/E2). P5–P7 not started. Details in PROGRESS.md.
All commands are PowerShell from the repo root.

## 1. Fix the model lines in `.env` (needed now)
Your `.env` was copied from the old template and pins models Groq no longer serves:
`EXTRACT_MODEL=llama-3.1-8b-instant`, `EXPLAIN_MODEL=...`, `AGENT_MODEL=llama-3.3-70b-versatile`,
`JUDGE_MODEL=gemini-2.0-flash`. **Make those four lines empty** (`EXTRACT_MODEL=`) so the defaults in
`config/settings.yaml` apply (gpt-oss-20b / gpt-oss-120b / qwen3.8-27b). Also blank `JUDGE_BASE_URL`.
Until then, every LLM command needs the overrides set in the same shell:
```powershell
$env:EXTRACT_MODEL="openai/gpt-oss-20b"; $env:EXPLAIN_MODEL="openai/gpt-oss-20b"; $env:AGENT_MODEL="openai/gpt-oss-120b"; $env:JUDGE_MODEL="qwen/qwen3.8-27b"
```
Note: keep the key only in `.env` (gitignored). It was pasted into `.env.example` (tracked); I moved it to
`.env` and restored the template before anything was committed.

## 2. Decide on LLM quota (blocks the full re-extraction)
Groq free tier ≈ 200K tokens/day per model → ~55 job extractions/day → ~18 days for all 987 jobs.
Options: (a) let it resume daily, (b) move to a paid Groq tier (whole run well under $1 at listed prices),
(c) only extract fresher-relevant jobs. The run is resumable and processes fresher-relevant/India jobs first:
```powershell
$env:DATABASE_URL="postgresql+psycopg://postgres@127.0.0.1:54329/joblens"
.\.venv\Scripts\python.exe -m app.pipeline --stale --steps facts,search
```

## 3. Review your profile
Draft from your resume: `data/private/profile_draft.json` (gitignored). Check especially:
- `experience_years` = 0 (your Outlier/Handshake work is part-time; set it if you count it),
- the skills list (it includes tools like "vs code", "jupyter", "google colab", "render" — remove what you don't want matched),
- target roles, locations, work modes, employment types, minimum pay: **not in the resume**; the saved dev
  profile uses the spec's 11.2 example values provisionally.
Then save it through the API (or the UI in P7): `PUT /profiles/<id>` with the id in `data/private/profile_saved.json`.

## 4. Review draft labels → write gold files (the eval numbers depend on this)
Drafts were written by `qwen/qwen3.8-27b` (a different family from the pipeline model). They are not hand labels.
- `evals/data/drafts/retrieval_labels_draft.jsonl` → after review, write `evals/data/retrieval_labels.jsonl`
  with `"status": "gold"` and `"labeler": "ravi"` (rules: `evals/guidelines.md`).
- `evals/data/drafts/extraction_gold_draft.jsonl` → `evals/data/extraction_gold.jsonl` (if not finished, resume:
  `.\.venv\Scripts\python.exe -m evals.draft_labels --suite extraction`).
- Replace the 10 synthetic pasted posts (`evals/data/drafts/paste_jobs_synthetic.jsonl`) with real ones you collect.
- Replace eval profile p01 in `evals/data/profiles.jsonl` with your real profile if you want it in E1 (it is committed — keep personal details out).
Then:
```powershell
.\.venv\Scripts\python.exe -m evals.run --suite retrieval
.\.venv\Scripts\python.exe -m evals.run --suite extraction --prompt facts_v1,facts_v2
.\.venv\Scripts\python.exe -m evals.run --suite all --report README_EVALS.md
```
Re-check 10% of your labels a day later: `.\.venv\Scripts\python.exe -m evals.pool --recheck`.

## 5. Docker Compose (verified 2026-11-19)
`docker compose up -d --build` works; `/health` is green. The db container is published on **host port 5433**
(your Postgres 17 service keeps 5432); inside the Compose network the api still uses `db:5432`.
If `docker` is not found in an old shell, open a new one (Docker Desktop adds itself to your user PATH).
```powershell
docker compose up -d --build
Invoke-RestMethod http://localhost:8000/health
docker compose run --rm api python -m app.ingest.run
# host tools against the Compose db:
$env:DATABASE_URL="postgresql+psycopg://joblens:joblens@localhost:5433/joblens"
```

## 6. Scam dataset (P5)
Download Kaggle "Real or Fake Job Posting Prediction" to `data/raw/fake_job_postings.csv` (check the license).

## 7. Deployment (P7+)
Render + Neon accounts and GitHub secrets: only you can create these.

## Local dev without Docker
```powershell
.\.venv\Scripts\python.exe scripts\devdb.py start > devdb.log
.\.venv\Scripts\python.exe -m pytest -q
```
