# JobLens build progress

Source of truth: `docs/JobLens_Project_Spec.pdf` (v1.0). Phases follow spec Section 20.

## Environment notes (apply to all phases)

- Docker Compose verified on 2026-11-19 (Docker 29.9.3, Compose v5.7.0): db (pgvector/pgvector:pg16,
  pgvector 0.8.7), api and ui (python:3.12-slim) all run; `/health` green. The db is published on host port
  **5433** because the machine's Postgres 17 service uses 5432 (left running).
- Stand-in for local runs/tests: `scripts/devdb.py` starts Postgres 16 + pgvector 0.6.2 from the
  `pgserver` pip package (dev-only, not in requirements.txt) on 127.0.0.1:54329. DB tests use it
  (`joblens_test` db) or `TEST_DATABASE_URL`; they skip if no DB is reachable.
- Python 3.12.16 venv in `.venv`, on a permanent interpreter at `C:\Users\racks\.uv-python` (moved off
  the temp folder on 2026-11-19; full suite re-run on it).
- Start the dev DB from a normal shell with its output redirected to a file
  (`.venv\Scripts\python.exe scripts\devdb.py start > devdb.log`). Piping its output hangs (the server keeps
  the pipe open); starting it from a hidden window makes Postgres child processes crash (0xC0000142).
- Session 1 had no LLM key (offline fake client, `fake-heuristic`). Session 2 added a Groq key: see below.
  Tests always run offline (conftest forces the fake LLM and fake embedder).

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

## P2 Extraction — done (offline heuristic; real-LLM run pending a key)

Built:
- Prompts `app/extract/prompts/`: `facts_v1` (Appendix A.1 verbatim), `facts_v2` (stricter evidence wording,
  BACHELORS/MASTERS/PHD codes, skill/experience guidance — the second prompt version for E2), `profile_v1`,
  `explain_v1`, `judge_v1`, `agent_v1` (Appendix A verbatim), `summary_v1`, `rerank_v1`.
- `app/extract/facts.py`: prompt → `chat_json` (temperature 0, cached) → shape coercion → Pydantic →
  up to 2 retries with the validation error appended → evidence check (whitespace/case-normalized
  substring; unsupported values set to null and logged as `evidence_missing`) → normalization (skill
  synonym map, degree codes, title-cased/canonical city names) → `job_facts` row with model,
  prompt_version, valid. Input capped at `(max_input_tokens-1500)*4` chars (≈6,000-token request).
- `app/extract/vocab.py` (synonyms, degree codes, specializations, Indian cities).
- `app/extract/heuristic.py`: rule-based extractor used by the fake LLM (`fake-heuristic`). Every value
  quotes a verbatim span. Red-flag rules skip negated / anti-scam-notice mentions.
- `app/extract/profile.py` + `app/api/routes_profiles.py`: `POST /profiles/from-resume` (pypdf; <200 chars →
  `resume_text_too_short`), `POST/GET/PUT/DELETE /profiles/{id}` (delete cascades chats, memory, explanations).
- `python -m app.pipeline [--steps facts,search,trust] [--all]` to (re)process stored jobs.

Tests: 65 passed. ruff + mypy clean.

Real run on the 987 dev jobs (fake-heuristic): 987/987 schema-valid, 0 evidence drops, 0 red flags
on company-board jobs (after fixing two false-positive patterns found by inspecting the output:
"WhatsApp" as a product name, and "we will never ask you to pay a fee" notices). Field coverage:
locations 89%, required_skills 73%, experience 66%, work_mode 60%, degrees 26%, pay 24%,
employment_type 16% (most ATS posts do not state it), graduation_years 2%, min_cgpa 0%.

Done-when:
- "90% of jobs have valid facts" ✔ for the heuristic (100%), but this is trivially true for a rule-based
  extractor; **the real-LLM rate is not measured yet** (no API key). Re-run: `python -m app.pipeline --steps facts --all`.
- "evidence failures logged" ✔ (`evidence_missing` log events; exercised in tests with bad evidence).
- "profile saved from a real resume" ✘ — tested end to end with a generated text PDF only; needs Ravi's resume.

Deviations:
- Evidence is checked against a "posting text" = `Title/Company/Location` header lines + description,
  not `description_text` alone, because ATS location/company are metadata fields, not in the text.
- Evidence matching also normalizes typographic quotes/dashes (in addition to whitespace and case).
- Degree codes extended with BACHELORS, MASTERS, PHD (spec list ends with "..."); eligibility treats
  BACHELORS as satisfied by any bachelor- or master-level code.
- Fake-LLM outputs use a per-process memory cache, never the `llm_cache` table (stale outputs after code changes).

## P3 Search + match — code done, live check incomplete

Built: `app/search/text.py` (raw + structured texts, profile query text per 11.2), `embed.py` (fastembed
MiniLM + bge-small, hashing fake for tests), `index.py` (pipeline step: search_text + 2 models x 2 variants),
`keyword.py` (FTS, websearch_to_tsquery, ts_rank_cd), `vector.py` (pgvector cosine), `hybrid.py` (RRF k=60,
top 50, max-normalized), `rank.py` (skill overlap + final score 11.4), `app/eligibility.py` (all 8 rules,
verdict logic, evidence per rule), `service.py`, `GET /search`, `GET /matches/{profile_id}`.

Tests: 110 passed (RRF/overlap/score hand-computed, every eligibility rule, DB end-to-end search/matches).

Not done: the real fastembed run over the 987 dev jobs was still in progress when the session stopped
(640/987 embedded). The live `/matches` check for the builder profile was not run.
Finish: `python -m app.pipeline --steps search --all`.

Additions: empty `preferred_locations` = no location preference (PASS); Delhi NCR cities treated as one
group; approximate FX table for the pay rule; `eligible_only` filter excludes INELIGIBLE only.

## Session 2 (2026-11-19)

### Step 1 — permanent venv ✔
`.venv` rebuilt on Python 3.12.16 at `C:\Users\racks\.uv-python`; full suite re-run (110 passed then, 124 now).

### Step 2 — real LLM (Groq): infrastructure ✔, full re-extraction PARTIAL (daily quota)
- The key had been pasted into `.env.example` (tracked). It was never committed; it is now only in `.env`
  (gitignored) and `.env.example` is back to the blank template. Config strips stray spaces/quotes around
  `.env` values (the key had a leading space).
- Groq's live model list (`scripts/list_models.py`) no longer has the Llama models from the old defaults.
  Chosen: extract/explain `openai/gpt-oss-20b` with `reasoning_effort: low` (reasoning tokens 1046 → 179 in
  a probe), agent `openai/gpt-oss-120b`, judge + draft labeler `qwen/qwen3.8-27b` (different family).
  **`.env` still pins the old Llama/Gemini names — blank those lines (NEEDS_RAVI §1).** Runs this session
  used process env overrides.
- Limits (headers + the provider's own 429 message): 8,000 tokens/min, 1,000 requests/day and
  **200,000 tokens/day per model**. One extraction ≈ 3–3.7k tokens → ~55 jobs/day → ~18 days for all jobs
  on the free tier.
- Added: shared tokens-per-minute pacer, 429 retry-after handling, clean stop on the daily limit
  (`DailyLimitReached`), Groq `json_validate_failed` → invalid attempt (retry path), and
  `python -m app.pipeline --stale` (resumable; fresher-relevant titles and India jobs first).
- Evidence checker fixes, found by inspecting drops (both keep the no-unsupported-value rule): ignore our
  own `- ` list markers; accept `...`-joined quotes only when every fragment is verbatim.
- **Results so far, `gpt-oss-20b` + `facts_v1`: 45 jobs extracted, 45/45 schema-valid (100%),
  49 evidence drops** (pilot 20 jobs: 17 drops; resumed run 25 jobs: 32 drops). Most drops are
  required/nice-to-have skills: the model writes long "quotes" (350–720 chars) that silently skip clauses,
  and these are correctly rejected. The run stopped at the daily cap
  (`TPD Limit 200000, Used 197680`); 920 jobs still have heuristic facts. Resume tomorrow with `--stale`.
- facts_v2 (one contiguous span ≤200 chars) on the pilot 20: 20/20 valid, 16 drops, 118 non-null fields
  kept vs 104 for v1. Not adopted: without gold labels the extra values may be right or hallucinated; E2 decides.

### Step 3 — gitignore + profile from resume ✔ (your review needed)
`.env`, `data/private/`, `data/raw/*` are ignored (checked with `git check-ignore`). Resume file is
`data/private/Ravi_Kumar_Prabudh_AIML_with_Experience.pdf` (not `resume.pdf`). Draft from `gpt-oss-20b`:
`data/private/profile_draft.json`. Saved to the dev DB (id in `data/private/profile_saved.json`); target
roles, locations, work modes and employment types are **provisional, from the spec's 11.2 example**,
because the resume does not state them. `experience_years` = 0 (only part-time work listed).

### Step 4 — cap fix ✔
Jobs are marked inactive only when gone from the board (full listing, not the capped selection); the cap
applies to new jobs only; changed content deletes stale `job_facts`. Live re-ingest: 87/87 boards,
7 deactivated (removed from boards), 5 new, 25 changed. Regression tests added.

### Step 5 — live /matches ✔ (quality poor until re-extraction lands)
Top 10 for the saved profile were mostly senior/lead roles: those jobs still had heuristic facts that
missed "N years" requirements, so the experience rule passed. Two real internships were #9–10.

### P4 eval harness ✔ (E1/E2); labels are drafts awaiting review
- `evals/run.py` (`--suite retrieval|extraction|all`, `--labels gold|drafts`, `--report`), `evals/metrics.py`
  (hand-checked tests), `evals/pool.py`, `evals/guidelines.md`, `evals/draft_labels.py`, `GET /evals/latest`.
  E3–E6 are registered as "not built yet (P5/P6)".
- Pool `evals/data/retrieval_pool.jsonl`: 847 pairs, 15 profiles, union of top-20 from M1–M5, shuffled,
  methods/scores hidden.
- Retrieval drafts (`evals/data/drafts/retrieval_labels_draft.jsonl`, qwen, 15 jobs per call, `"status":
  "draft"` + reason each): **complete, 847/847 pairs, 0 failures**, 64 calls, 158,087 tokens
  (121,190 in / 36,897 out, ~187 per pair), 36 min. Distribution: 713 × 0, 118 × 1, 16 × 2. Few 2s because
  the ATS boards are dominated by senior roles. The harness was run once on these drafts to check it
  works end to end (`--labels drafts`, output bannered DRAFT); those numbers were not kept or committed.
  Scope check requested on 2026-11-19: at 553/847 pairs, 100,162 tokens (~186/pair), ETA ~13 min
  (< 30 min threshold), so the full-scope job was left running rather than reduced. Drafting is resumable
  (skips pairs already in the draft file). Note: drafting (qwen) and extraction (gpt-oss-20b) did run at the
  same time for ~20 min earlier. Groq's limits appear to be per model (the 429 names
  `openai/gpt-oss-20b`; qwen's counter was separate), so they did not compete for tokens. Still, from now on
  only one LLM job runs at a time.
- Extraction drafts: not started (qwen's remaining daily quota is used by the retrieval drafts).
- 10 synthetic Indian-style paste posts in `evals/data/drafts/paste_jobs_synthetic.jsonl` (marked synthetic).
- No gold files exist yet, so there are **no real E1/E2 numbers** in this repo.

## Session 3 (2026-11-19)

### Step 1 — Docker ✔
`docker compose up -d --build`: all three containers up; `GET /health` → `status: ok`, db ok, pgvector 0.8.7.
Port 5432 was held by the local Postgres 17 service, so the db container maps host 5433 → container 5432
(`docker-compose.yml`, `.env.example`, NEEDS_RAVI updated); the system service was not touched. The ui
container failed because `ui/streamlit_app.py` did not exist yet (P7); added a minimal status page (API
status line + cold-start wait, spec 17) so the stack starts cleanly. The real pages are still P7.

### Step 2 — job pool fixed for freshers ✔ (no LLM calls)
- **Seniority filter** (`app/ingest/seniority.py`, migration 0002 `jobs.is_fresher_relevant` +
  `seniority_reason`): title has senior/sr/staff/principal/lead/manager/director/head/architect (also
  vp/chief, levels III+, SDE3), or the text asks for X+ years with X >= 3 → not fresher-relevant. Junior
  titles (intern, graduate, trainee, ...) override the soft words (lead/manager/head/architect), so
  "Product Manager Intern" and "Associate Product Manager" stay. Rows stay in the DB; new senior jobs are
  not added at ingestion; the per-company cap counts only fresher-relevant jobs.
- **India eligibility** (your mid-run request; migration 0003 `is_india_eligible`, `location_reason`,
  generated `in_scope = is_fresher_relevant AND is_india_eligible`): location parts are checked
  separately — any Indian place → eligible; only generic remote terms (Remote, Global, Anywhere, APAC) →
  eligible; a remote listing next to or qualified by another place ("Remote - US", "Vienna, Austria /
  Remote", "Remote, AMER") → not India-eligible. Added Indian states and more cities (Patna was missed).
- Matching, `/search`, evals and extraction use `in_scope` by default (`include_out_of_scope=true` /
  `--include-out-of-scope` to override).
- **More companies**: `scripts/verify_more_companies.py` probed 154 new candidates (every token with a real
  request): 44 returned 200, 23 kept with >= 1 fresher-relevant India/remote job → 110 boards.
- Counts (active jobs, dev DB): before 206 fresher-relevant (of 985) → after new boards 466 (of 1,245) →
  after India eligibility **341 in scope** (of 1,249): 270+ India locations, ~67 open remote; only 24 are
  internships and ~5 new-grad titles — most in-scope jobs simply have no seniority signal (this includes
  non-tech roles such as account executives). Spot-check of 20 excluded / 20 included was shown to you;
  it is what motivated the India-eligibility rule.

### Step 3 — Gemini as second provider ✔
Per-role providers (`role_providers` in settings): extract → Gemini `gemini-3.5-flash-lite`;
profile (resume), explain, agent, judge/drafts → Groq. One rate limiter per provider endpoint
(tokens/min + requests/min; Gemini 10 req/min, since it sends no rate-limit headers). Gemini daily-quota
429s stop the run cleanly. Model choice from Gemini's live list: `gemini-3.5-flash-lite` probe was
schema-valid with 0 evidence drops (2.3k tokens, 11.8 s); `gemini-3.8-flash` timed out at 90 s.

### Step 4 — extraction on Gemini ✔ (341/341 in-scope jobs)
- `python -m app.pipeline --stale --steps facts,search` on `gemini-3.5-flash-lite` (facts_v1), fresher-relevant
  and India first, resumable. Took three runs: (1) stopped by me after ~70 jobs because Gemini wraps
  `title`/`company` as `{value, evidence}` and every job needed a retry (fixed in `coerce_raw`); (2) crashed
  at ~150 jobs on a Gemini **HTTP 503 "high demand"** (fixed: back-off on 5xx/timeouts, and one failing job
  no longer stops the run); (3) finished the rest with 0 rate-limit hits and 0 failed jobs.
- **Result: 341/341 schema-valid after retries (100%); 86 evidence drops vs 1,619 kept values → 5.0% drop
  rate.** 429 Gemini calls, 1.21M input / 0.20M output tokens. Drops: required_skills 61,
  nice_to_have 16, locations 6, other 3. 99 attempts were retried (title/company wrapping, then bare
  `"field": null`; the latter is now also coerced).
- **Head-to-head on the 25 jobs both models extracted** (same text and prompt, replayed from `llm_cache`, scored
  with the current checker, `scripts/compare_models.py`): gpt-oss-20b 25/25 valid, 125 values kept,
  20 dropped (13.8%), drops in 13/25 jobs; gemini-3.5-flash-lite 25/25 valid, 141 kept, 10 dropped (6.6%),
  drops in 6/25 jobs. This measures grounding, not accuracy; E2 with gold labels will compare accuracy.
- gpt-oss-20b outputs are kept in `llm_cache` (only `job_facts` rows were replaced), so
  `python -m evals.run --suite extraction --model openai/gpt-oss-20b,gemini-3.5-flash-lite` reuses them;
  the eval routes each model to the provider that serves it.
Evidence-drop audit (`scripts/audit_evidence_drops.py`, replayed from `llm_cache`, no API calls) of the
real gpt-oss-20b extractions: 52 jobs, 56 drops (more than the 45/49 reported earlier because the pilot
re-scoring runs added jobs). 15 were formatting-only and the check was too strict: U+2011 non-breaking
hyphen vs "-", quote marks wrapped around the evidence, "; "/". " inserted where our bullet line breaks
were, and a ". ." artifact from our HTML-to-text step. Fixed (in-token punctuation kept, so "7.0" ≠ "70").
41 stay dropped and are genuinely wrong: 38 quotes not in the posting (long multi-sentence quotes that
skip words or clauses, and invented ones such as "Job is a senior leadership role in a SaaS company" or
"Full-time" where the posting never says it) and 3 values with no evidence.

### Step 5 — existing retrieval drafts after the pool change (not redrafted)
Of 847 drafted pairs: **139 still valid** (job active and in scope), 677 now out of scope as senior,
31 as not open to India, 0 inactive. The filter agrees with the drafts: it removed 660 of the 713
drafted 0s but only 1 of the 16 drafted 2s. Valid pairs by relevance: 15 × 2, 71 × 1, 53 × 0. A rebuilt
in-scope pool would have 786 pairs; all 139 are in it, so 647 would need new drafts. Waiting for your go-ahead.

## Session 4 (2026-11-19)

### Role-family filter ✔ (rule-based, no LLM)
`app/ingest/role_family.py` + migration 0004: `jobs.role_family` ∈ engineering | data_ml | product | design | other
(+ `role_reason`), from title keywords; for generic titles (Intern, Associate, Graduate, Trainee) the extracted
required skills break the tie unless the title has a non-tech word (marketing, sales, talent, ...). Customer-facing
"engineer" titles (sales/solutions/support/field/customer engineer) count as other. Set at ingestion (title) and
refined after extraction (title + skills). `in_scope` now = fresher-relevant AND open to India AND family in
(engineering, data_ml, product); all jobs stay in the DB; only in-target jobs are added at ingestion.
- Active jobs by family (1,249): other 733, engineering 341, data_ml 105, product 53, design 17.
- In scope before new boards: **117** (91 engineering, 23 data_ml, 3 product) — under 150, so:
- Round 3 boards: 113 tokens probed (incl. tokens seen in real public posting URLs via web search), 23 HTTP 200,
  14 kept with >= 1 tech fresher India/remote job → 124 boards. Re-ingest: 38 new, 7 deactivated (removed from boards).
- **In scope now: 155** (118 engineering, 29 data_ml, 8 product); 109 India locations, 46 open remote;
  **12 internships, 2 new-grad/entry titles**. All 155 extracted on Gemini (38 new: 38/38 valid on the first
  attempt, 17 evidence drops).

### Review items
- Profile (dev DB id in `data/private/profile_saved.json`): degree MCA (AI & IoT), graduation 2027, CGPA 7.96,
  experience 0, 27 skills — all from the resume via Groq gpt-oss-20b (real LLM). target_roles, preferred_locations,
  work_modes, employment_types are provisional (spec 11.2 example); min pay unset.
- Live `/matches` top 10 (dev DB, real stack: fastembed query embedding, Gemini facts, rule-based eligibility;
  no fake client): all 10 in scope, all CHECK (degree/CGPA/batch mostly unstated), 4 are intern/graduate roles.
  Trust level is "not computed" for all — P5 (trust) is not built yet. A first attempt hit a stale API process
  from this morning still bound to port 8011 (old code, no filters); that output was discarded and the process stopped.
- Cap fix (live jobs wrongly marked inactive): **done** in 46fd8fc — deactivation uses the full board listing;
  later changed so the cap counts only in-target jobs. Every re-ingest since has deactivated only jobs removed
  from their boards (7, 0, 7).

### Redraft estimate (not started — waiting for confirmation)
5 profiles (yours + p05 ML intern, p09 full-stack, p10 data engineer, p15 GenAI), in-scope pool, top-10 per method
(M1–M5): 130 pairs, 26 reusable from existing drafts → **104 pairs to draft = 10–13 qwen calls at 10–15 jobs per
call, ~19K tokens** (~187 tokens/pair measured). Your profile is not in the committed `evals/data/profiles.jsonl`;
proposal: add it as an anonymized `p00` (no name) before drafting.

## Session 5 (2026-11-19)

- **Profile:** you confirmed all fields; added skills `llm evaluation`, `prompt engineering` (29 skills). Dev-DB
  profile updated; `data/private/profile_saved.json` records the confirmation.
- **p00** added to `evals/data/profiles.jsonl`: your profile without name or contact details.
- **Drafts v2:** `evals/data/retrieval_pool_v2.jsonl` (5 profiles p00/p05/p09/p10/p15, tech-only in-scope pool,
  top-10 × M1–M5 = 130 pairs; 26 reuse earlier drafts). qwen/qwen3.8-27b, batches of 13: **104/104 drafted,
  0 failed, 10 calls, 19,784 tokens**, nothing else running. All `status: draft` with a reason.
  p00: 7 × 2, 5 × 1, 11 × 0. Spotted while reading them: "Wikimedia… location is US" (it is remote); a
  Hyderabad job labelled 0 for location alone (guidelines cap that at 1); and two seniority-filter leaks
  in the pool ("Software Engineer 3", an Acceldata job asking for 4+ years).
- **Pasted postings** (`scripts/paste_files.py`, real `POST /jobs/paste`, Gemini): `elip.txt` (job 1295) and
  `spectovx.txt` (job 1296) extracted, valid. Problems found, not fixed yet:
  1. `guess_title` takes the first line, so both titles are "Company: …" → role family "other" → out of scope,
     they will not appear in matches. (Bug in paste.py.)
  2. SpectoVX `graduation_years = [2027]` comes from "January to June 2027" (internship dates, not a batch):
     a verbatim quote with a wrong reading, which the evidence check cannot catch. The eligibility line
     "graduation_year PASS" rests on it.
  3. SpectoVX "All specializations are welcome" was not extracted (`specializations` null).
  4. Elip "on-site or remote" was extracted as `work_mode = hybrid`.
  5. Skills come out as phrases ("python backends", "llms and agents"), so overlap with profile skills is poor.
  Trust: not computed for either — P5 is not built.
- **`jnj_multi_role.txt` is empty (0 bytes)**, so the multi-role case was not run. The current schema holds one
  title and one set of facts per job, so a 4-role post would become one job. Proposed fix (awaiting approval):
  split such posts into one job per role, with the shared header (CGPA, branches) copied into each.

## Not started: P5, P6, P7.
