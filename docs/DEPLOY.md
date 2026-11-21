# Deploying the public demo

The hosted demo has three parts: **Neon** (Postgres with pgvector), **Render** (the API, free web service) and
**Streamlit Community Cloud** (the UI). All three have free plans; Render and Neon need an account, Streamlit Cloud
signs in with GitHub. Nothing here needs a code change.

Every secret goes into a provider dashboard. None goes into the repository, `.env.example`, `settings.yaml` or a
committed `.streamlit/secrets.toml`.

## What runs where

| Part | Host | Measured memory (local, Windows) |
|---|---|---|
| API (`Dockerfile`, last stage `api`) | Render free web service, 512 MB | about 310 MB peak in public-demo mode (MiniLM only) |
| UI (`ui/streamlit_app.py`) | Streamlit Community Cloud | about 130 MB peak |
| Database | Neon free | about 29 MB after seeding |
| Job refresh (ingest) | your own machine, pointed at Neon | not on the host: a batch of 128 jobs needs about 460 MB |

Free-plan behaviour to expect: Render spins a free service down after 15 minutes without traffic and takes about a
minute to wake up; it grants 750 free instance hours per workspace per month. Streamlit Community Cloud puts an app
to sleep after 12 hours without visitors. Check the providers' pages for current limits before you rely on them.

## Secrets: which value goes where

| Name | Where | Value |
|---|---|---|
| `DATABASE_URL` | Render → Environment | the Neon connection string, with the scheme changed to `postgresql+psycopg://` |
| `PUBLIC_DEMO` | Render → Environment | `true` |
| `UI_API_KEY` | Render → Environment **and** Streamlit → Secrets | the same random string, 12+ characters |
| `LLM_API_KEY` | Render → Environment | your Groq key (chat, explanations, resume parsing) |
| `API_URL` | Streamlit → Secrets | the Render service URL, e.g. `https://joblens-api.onrender.com` |
| `ADMIN_API_KEY` | **nowhere** | leave it unset on Render: admin routes then refuse every call, so no one can start an ingest on the host |
| `GEMINI_API_KEY` | not needed on Render | Gemini only extracts requirements from postings, which happens during ingest and paste; ingest runs on your machine and paste is off in the demo |

`PUBLIC_DEMO=true` turns on: MiniLM only (no other embedding model can be loaded), pasting jobs off, and the
UI-to-API key check on every route except `/health`. If `UI_API_KEY` is missing or shorter than 12 characters in
public-demo mode, the API refuses every request instead of running open.

## Step by step

### 1. Neon

1. Create a Neon account and a project. Pick the region closest to your Render region.
2. Copy the connection string from the project dashboard (it looks like
   `postgresql://USER:PASSWORD@HOST/neondb?sslmode=require`). Keep it out of files and chat logs.

pgvector does not need to be enabled by hand: the first migration runs `CREATE EXTENSION IF NOT EXISTS vector`.

### 2. Seed Neon from your local database (on your machine)

The seed script migrates the target, refuses a target that already has jobs, and copies only: job-board jobs (no
pasted jobs), their extracted facts and trust scores, and their MiniLM embeddings. It never copies profiles, chat,
memory, explanations, the LLM cache, rate-limit counters or eval runs. The URLs come from the environment, so they
stay out of your shell history. In PowerShell, with the local dev database running (`python scripts/devdb.py start`):

```powershell
$env:SEED_SOURCE_URL = "postgresql+psycopg://postgres@127.0.0.1:54329/joblens"
$env:SEED_TARGET_URL = Read-Host "Neon connection string"
python scripts/seed_prod.py
```

The first run is a dry run that prints the row counts. If they look right:

```powershell
python scripts/seed_prod.py --apply
Remove-Item Env:SEED_TARGET_URL
```

It ends with `copied and verified`. A local run of this copy gave 1,294 jobs, 1,206 facts, 113 trust scores and
2,580 embeddings.

### 3. Make the UI-to-API key

```powershell
python -c "import secrets; print(secrets.token_hex(24))"
```

You will paste this value twice (Render and Streamlit). Don't save it in the repository.

### 4. Render (API)

1. Create a Render account and connect GitHub.
2. **New → Web Service**, pick the JobLens repository, branch `main`.
3. Language: **Docker**. Dockerfile path: `./Dockerfile` (the default). Render builds the last stage, which is the
   API. It sets `PORT` itself, and the container listens on it.
4. Instance type: **Free**.
5. Environment variables: `DATABASE_URL` (the Neon string, starting with `postgresql+psycopg://` instead of
   `postgresql://`), `PUBLIC_DEMO=true`, `UI_API_KEY`, `LLM_API_KEY`.
6. If the form offers a health check path, set it to `/health`.
7. Click **Deploy**. The first build takes several minutes.
8. Open `https://<your-service>.onrender.com/health`. Expect `"status": "ok"` and `"public_demo": true`.
   `"scam_classifier": {"present": false}` is expected: the trained classifier is not in the repository, so trust
   uses the rule checks and ghost-job signals only.
9. Check that the key works: `https://<your-service>.onrender.com/limits` in a browser must answer
   `ui_key_required` (HTTP 401).

The MiniLM model (about 87 MB) is downloaded on the first search after each start, because Render's free disk is
not kept. With the wake-up time, the first page load after a quiet period can take about a minute.

### 5. Streamlit Community Cloud (UI)

1. Sign in at share.streamlit.io with GitHub.
2. Create a new app from GitHub: repository JobLens, branch `main`, main file path `ui/streamlit_app.py`.
3. **Advanced settings**: Python version **3.12**. In **Secrets**, paste:

   ```toml
   API_URL = "https://<your-service>.onrender.com"
   UI_API_KEY = "<the key from step 3>"
   ```

   Keep both at the top level (not under a `[section]`): Community Cloud exposes top-level secrets as environment
   variables, which is where the UI reads them.
4. Click **Deploy**. Community Cloud installs `ui/requirements.txt` (it looks next to the entrypoint first), not the
   API's root `requirements.txt`.

### 6. Check the live demo

1. Open the app. The sidebar shows "API is waking up" until Render answers.
2. Matches, Job detail, Profile, Chat and Evals should work. The Paste page shows "Pasting jobs is turned off in
   the public demo".
3. **Check that the per-visitor limit sees real visitors.** Use the chat once from your laptop and once from your
   phone on mobile data (not the same Wi-Fi). Then, in the Neon SQL editor:

   ```sql
   SELECT action, key, count FROM usage_counts WHERE day = current_date ORDER BY action, key;
   ```

   You should see two different `ip:…` keys for `chat`. If both requests share one `ip:…` key, Streamlit's hosting
   puts more than one proxy in front of the app: set `VISITOR_PROXY_HOPS=2` on Render and check again. If you see
   `ip:unknown`, the visitor address did not reach the API: all visitors then share one per-IP allowance, so keep
   the demo link private until that is fixed (the per-session and per-day limits and the token budgets still apply).

## Limits that stay on in the demo

- Per visitor IP, per browser session and for the whole app, per UTC day (`limits` in `config/settings.yaml`):
  chat 40 / 20 / 300, explain 30 / 15 / 150, resume 6 / 3 / 30.
- Daily LLM token budgets per purpose (`budget`): agent 180K, explain 40K, summary 20K, others 300K.
- Visitor IPs are stored only as a keyed hash in `usage_counts`.

## Refreshing the jobs later (on your machine)

Run ingest locally against Neon, in public-demo mode so only MiniLM embeddings are written. It uses the LLM keys
from your local `.env` for extraction.

```powershell
$env:DATABASE_URL = Read-Host "Neon connection string (postgresql+psycopg://...)"
$env:PUBLIC_DEMO = "true"
python -m app.ingest.run
Remove-Item Env:DATABASE_URL, Env:PUBLIC_DEMO
```

## Rolling back

Suspend the Render service and the Streamlit app from their dashboards; the Neon project can be deleted from its
settings page. Rotate `UI_API_KEY` and the Groq key if either was ever exposed.
