# What only you can do (JobLens)

Build stopped at the end of P3 because the session's usage limit was reached. Phases P4–P7 are not built yet.

1. **Install Docker Desktop** (not on this machine, so Compose was never run). Then, in PowerShell:
   ```powershell
   Copy-Item .env.example .env
   docker compose up -d --build
   Invoke-RestMethod http://localhost:8000/health
   docker compose run --rm api python -m app.ingest.run
   ```
2. **LLM API key**: put `LLM_API_KEY` (and optionally `JUDGE_API_KEY`) in `.env`. Then re-extract with the real model:
   ```powershell
   docker compose run --rm api python -m app.pipeline --all
   ```
3. **Your real profile/resume**: replace p01 in `evals/data/profiles.jsonl`, and test `POST /profiles/from-resume` with your PDF.
4. **Scam dataset** (for P5): download the Kaggle "Real or Fake Job Posting Prediction" CSV to `data/raw/fake_job_postings.csv`. Check the license first.
5. **Label review** (P4, once built): drafts will go in `evals/data/drafts/`. You write the gold files.
6. **Deployment** (Render + Neon, GitHub secrets): not set up. Only you can create these accounts.

Local dev without Docker (what this build used):
```powershell
.\.venv\Scripts\python.exe scripts\devdb.py start
$env:DATABASE_URL="postgresql+psycopg://postgres@127.0.0.1:54329/joblens"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m app.pipeline --steps search --all
```
