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
