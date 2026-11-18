# JobLens build progress

Source of truth: `docs/JobLens_Project_Spec.pdf` (v1.0). Phases follow spec Section 20.

## Environment notes (apply to all phases)

- **Docker is not installed on the build machine**, so `docker compose up` could not be run here.
  The Dockerfile / docker-compose.yml are written to spec (python:3.12-slim for api and ui,
  pgvector/pgvector:pg16 for db) but are **unverified**. See NEEDS_RAVI.md.
- Stand-in for local runs/tests: `scripts/devdb.py` starts Postgres 16 + pgvector 0.6.2 from the
  `pgserver` pip package (dev-only, not in requirements.txt) on 127.0.0.1:54329. DB tests use it
  (`joblens_test` db) or `TEST_DATABASE_URL`; they skip if no DB is reachable.
- Python 3.12.16 venv in `.venv` (created with uv). The interpreter lives in a temp folder on this
  machine, so recreate the venv if it breaks: `uv venv --python 3.12 .venv`.
- **No `.env` / LLM key was present.** Everything runs on the offline fake LLM client
  (`LLM_PROVIDER` resolves to `fake`; model name `fake-heuristic`). See each phase for what this means.

## P0 Setup — done (Docker unverified)

Built:
- Repo layout per Section 6; `pyproject.toml` (ruff, mypy non-strict, pytest config).
- `config/settings.yaml` (all weights/thresholds/models/prompt versions, `${VAR:-default}` expansion),
  `app/config.py` (pydantic-settings for `.env` + typed YAML), `.env.example` (no secrets).
- `app/models.py` + Alembic migration `0001_initial` containing the Section 8 DDL.
- `app/llm/client.py`: `chat()`, `chat_json()`, `chat_tools()` over the openai SDK with `base_url`;
  timeouts + retries; cache by `sha256(model|prompt_version|input)` (`app/llm/cache.py`, DB table
  `llm_cache` or memory); JSON log line per call (model, prompt_version, tokens, latency, cache hit);
  daily token budget that blocks non-essential calls.
- `app/llm/fake.py`: deterministic offline provider that routes by prompt_version to rule-based handlers.
- `/health` (DB, pgvector, LLM provider, embeddings, classifier), request-id middleware, JSON logs,
  uniform error shape `{"error": {"code", "message"}}`.
- Dockerfile (targets `api`, `ui`), docker-compose.yml, Makefile, `.github/workflows/ci.yml`
  (Postgres service + ruff + mypy + pytest).

Tests: 16 passed (`pytest -q`), ruff clean, mypy clean. Live check: `uvicorn` against the dev DB
returned `/health` → `{"status": "ok", "db": {"ok": true, "pgvector": "0.6.2"}}`.

Done-when check: `/health` green ✔ (local uvicorn) · `docker compose up` ✘ not runnable here ·
CI passes — workflow written, not run (no push, by instruction).

Deviations:
- `chat_sessions.profile_id` and `chat_messages.session_id` got `ON DELETE CASCADE` (not in the
  Section 8 DDL) so that `DELETE /profiles/{id}` removes chats as Section 10 requires.
- `chat_json()` uses provider JSON mode (`response_format=json_object`) with the JSON Schema in the
  prompt (Appendix A.1 already includes `{json_schema}`), because strict schema mode is not
  supported uniformly across OpenAI-compatible providers. Pydantic validates the result.
- HTTP and DB access is synchronous (httpx.Client, sync SQLAlchemy) for simplicity; FastAPI runs
  sync routes in its threadpool.

## P1 Ingestion — done

Built:
- `app/ingest/greenhouse.py`, `lever.py`, `ashby.py`: fetch + map to `NormalizedJob` (endpoints and field
  names checked against real responses: Greenhouse `first_published`/`content` (entity-escaped HTML),
  Lever `createdAt`/`lists`/`workplaceType`, Ashby `isRemote`/`secondaryLocations`/`compensation`).
- `app/ingest/http.py`: polite client — global 2 req/s limiter, 15 s timeout, 3 retries with
  exponential backoff on 429/5xx/transport errors, User-Agent `JobLens/0.1 (...)` (no email).
- `app/ingest/normalize.py`: HTML→text (list items as `- ` lines, whitespace collapse, 20,000-char cap),
  `content_hash`, India/remote location filter.
- `app/ingest/store.py`: upsert with first/last seen, `is_active=false` for a company's unseen jobs,
  re-processing on content-hash change, repost detection (same normalized company+title within 90 days).
- `app/ingest/run.py` (`python -m app.ingest.run`), `app/ingest/paste.py`, `POST /jobs/paste`,
  `GET /jobs/{id}`, `POST /admin/ingest` (X-API-Key), `app/pipeline.py` (steps plug in from P2 on).
- `config/companies.yaml`: **87 companies, every token verified with a real HTTP request** by
  `scripts/verify_companies.py` (257 probes; 120 returned 200 with a valid payload; kept the 87 with
  ≥1 India/remote job). Full probe log incl. failures: `config/companies_verification.json`.
- Recorded real API responses for tests: `tests/fixtures/{greenhouse,lever,ashby}_board.json`
  (`scripts/record_fixtures.py`).

Tests: 42 passed (respx-mocked boards from recorded fixtures; DB tests for upsert/idempotency/
deactivation/reposts/paste/admin auth). ruff + mypy clean.

Real run (dev DB, 2026-11-18): 87/87 companies OK, **987 active jobs stored**, 10,990 filtered out
(non-India/non-remote or over the cap). Re-run: 0 new, 987 unchanged, 0 deactivated → idempotent.

Done-when: 300+ active jobs ✔ (987) · re-run idempotent ✔ · tests use fixtures ✔.

Deviations / additions:
- Location filter (settings `ingest.india_keywords/remote_keywords/remote_exclude`): keeps India
  locations and remote roles without a non-India restriction. The spec has `country_hint` "used for
  filtering" but no rule; this is the rule chosen.
- `ingest.max_jobs_per_company: 20` (India first, then newest) keeps the pool inside the spec's
  300–1,000 target; without it the verified boards give ~2,000+ India/remote jobs.
  Known limitation: if a company posts new jobs that push an older one out of its top 20, the older
  one is marked inactive even if still live.
- Paste jobs without a company store `company=""` (column is NOT NULL); trust scoring treats it as missing.
