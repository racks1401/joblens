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

## Session 6 (2026-11-19)

1. `data/pasted/` is gitignored.
2. **Paste title fix:** a "Company: X" line becomes the company and is never the title; headers, headings and
   bullets are skipped; pasted jobs are extracted without a guessed title and `JobFacts.title` becomes the job
   title. Elip → "Software Engineer", engineering, in scope, **rank 3 in your matches (score 53)**. SpectoVX →
   "Intern" (the post has no role title, only "Tracks: AI | App | Web"), role family other, still out of scope.
   New issue found in step 4: the extracted title is not evidence-checked, and for JNJ the model invented
   "Johnson & Johnson Hiring" (not in the post).
3. **Prompt `facts_v3`** = facts_v1 + two rules (batch only from graduation/pass-out phrases; "all
   specializations welcome" → ["all"]); now the configured prompt. SpectoVX before → after: graduation_years
   [2027] (from "January to June 2027") → null; specializations null → ["all"]. Other jobs keep facts_v1 facts
   until a `--stale` re-run (~160 Gemini calls; not started).
4. **JNJ multi-role post** (4 roles: 3 headed — JJT Internship, TLDP, Product Engineer — and one unheaded PG-only
   internship): one job (1297). Extracted the JJT Internship's type, stipend (₹75k) and tenure; kept the shared
   CGPA 7.5, Hyderabad, deadline. Lost TLDP (full-time, 15.65 LPA) and Product Engineer (15.52 LPA) and the
   4th role's PG-only rule. Merged/misread: degrees [MTECH, MCA] from an "Under Graduate & Post Graduate"
   sentence (drops UG), specializations = shared branches ∪ "like …" examples (incl. "mca"). Company empty (no
   "Company:" line). Your eligibility: CHECK (degree, branch, CGPA 7.5 ≤ 7.96 pass; Hyderabad soft-fail; batch
   unknown). **Proposed fix (not implemented):** split a multi-role post into one pasted job per role, the
   shared header and footer copied verbatim into each; split points chosen by an LLM that returns only line
   numbers, so every piece is verbatim original text; an unheaded block is flagged for you rather than guessed.
5. **Seniority misses:** 47 of 156 in-scope jobs. Causes: (A) "X+ years in/building …" without "experience"
   nearby; (B) the minimum of all numbers is used ("5+ years …, at least 2 years in X" → 2); (C) numeric levels
   (Engineer 2/3, SDE2, SDE II, SDE - 3) not covered; (D) years in the title not checked; (E) Gemini's evidenced
   `experience_min_years` not used. **Proposed fix (not applied):** years phrasing without "experience", first
   requirement instead of the minimum, title level ≥ 3 = senior, years check on the title, and after extraction
   `experience_min_years` ≥ 3 → not fresher-relevant. Expected effect: in scope ~156 → ~109 (below 150).
6. **p00 drafts printed for review** (23): 7 × 2, 5 × 1, 11 × 0. Flagged: Wikimedia ("location is US"; it is
   remote) and Zscaler (0 for location only; guidelines cap a location mismatch at 1). No labels changed.

## Session 7 (2026-11-19)

1. **Seniority fix applied (all five causes)**: years phrasing without "experience", first requirement instead
   of the minimum, title levels >= 3 (Engineer 3, SDE - 3, III, L4+), years in the title, and an evidenced
   extracted `experience_min_years` >= 3 (junior titles excepted). Backfill: in scope 156 → 116, 40 marked not
   fresher-relevant, 0 rows deleted. Level-2 titles asking ≤ 2 years stay in scope by design.
2. **Pasted-job titles**: the extracted title is accepted only if it appears in the posting; otherwise
   "Title unknown" (flag on `GET /jobs/{id}` → `flags`). Migration 0005: pasted jobs are always in scope (skip
   the seniority, location and role-family filters). SpectoVX → "Title unknown", in scope, but not in your
   top 50 (keyword search misses it; vector rank 36, hybrid rank 90 of 118). Elip stays "Software Engineer", rank 4.
3. **Multi-role splitter** (`app/ingest/split.py`, prompt `split_v1`): LLM returns only line ranges; validated;
   pieces = original header + role + footer lines; unheaded role → "Needs human title". 9 tests.
   `data/pasted/jnj_multi_role.txt` did **not** contain the "Company: Johnson & Johnson" line when run (first
   line still `*Johnson & Johnson*`), so company is empty. Result: 4 jobs — JJT Internship (₹75k/month),
   TLDP (pay 15.65 read without its LPA unit), Product Engineer (pay 15.52 with currency "LPA"), and one flagged
   "needs human title" (₹75k/month, PG-only). All CHECK for you (Hyderabad soft-fail). Remaining extraction gaps
   in the pieces: LPA units, UG dropped from "Under Graduate & Post Graduate", branch list mixes "like …" examples
   and degree codes. The earlier unsplit JNJ job 1297 was marked inactive (superseded).
4. **Re-extraction on facts_v3**: 115/115 in-scope jobs, all valid, 37 evidence drops, no rate or daily
   limit hit, 0 left. In scope now **120**, all on facts_v3.
5. **Pool v3** (`evals/data/retrieval_pool_v3.jsonl`, 5 profiles, top-10 × M1–M5): 135 pairs; 83 carried over,
   47 v2 pairs dropped, **52 new drafted** on qwen (5 calls, 10,128 tokens). All 135 have a draft.
6. **p00 draft fixes** (from re-reading the posting text; still `status: draft`, previous label kept):
   Wikimedia 1 → 0 ("Location: Remote (US)*" — the qwen reason was right; it is US-only), Zscaler 0 → 1 (2+ yrs
   Java + hybrid Hyderabad: each a stretch, capped at 1), Sarvam SDE2 and Canonical Python Engineer: reasons
   replaced with evidence-based ones.
   New finding: the India-eligibility rule reads only the ATS location field ("Remote"), not "Location:" lines
   in the description ("Remote (US)"). Not fixed yet.

## Session 8 (2026-11-19)

1. **India location from the posting text**: for an "open remote" board listing, "Location: …" lines and phrases
   such as "Remote (US)", "remote within the US", "must be based in X", "authorized to work in X" restrict it, but
   only when they name a known non-India country/region (positive identification). The dry run of a first version
   would have wrongly excluded 10 Canonical "globally remote / every time zone" jobs; fixed before applying.
   Applied: in scope 120 → **119** (Wikimedia internship, "Location: Remote (US)*"); no rows deleted. 12 tests.
2. **Pasted/split jobs**:
   a. Lakh/LPA pay → full INR in code (only when the pay quote says LPA/lakh and the number is lakh-sized):
      TLDP 15.65 "INR/year" → ₹15,65,000/year; Product Engineer 15.52 "LPA" → ₹15,52,000/year.
   b. `facts_v4` = facts_v3 + (both degree levels when UG and PG are allowed; branches only from the eligible
      list, no "like …" examples, no degree names). New setting `prompts.facts_paste: facts_v4`; `--stale`
      compares each job with its own prompt. Re-extracted only the 6 pasted jobs (6/6 valid, 0 drops).
      Before → after: JNJ degrees [MTECH, MCA] → [BACHELORS, MASTERS, MCA, MTECH] (PG-only piece: [MASTERS, MCA,
      MTECH]); branches (10 incl. examples and "mca") → [computer science, electronics, ee, mnc]; TLDP/Product
      Engineer batch [2027] → null; TLDP type null → full_time (from "CTC: 15.65 LPA", an inference); SpectoVX
      title → "AI | App Development | Web Development" (a verbatim "Tracks" line, so the title rule accepted it —
      it is a track list, not a role); Elip work_mode onsite → remote for "on-site or remote" (the schema holds
      one mode, so both readings are incomplete). "ee"/"mnc" are not normalized (no synonym yet).
   **Prompt per job:** facts_v4 — the 6 pasted jobs (1295 Elip, 1296 SpectoVX, 1298–1301 JNJ pieces);
   facts_v3 — all 113 in-scope board jobs (ashby 22, greenhouse 76, lever 15).
3. **Pool v4** (`evals/data/retrieval_pool_v4.jsonl`, 134 pairs): 131 carried from v3, 4 dropped, 2 new drafted
   (2 qwen calls, 960 tokens). All pairs have a draft.
4. **Review tool** `scripts/review_labels.py` (`--profile p00`, `--sample N`, `--pool`): shows requirements with
   evidence quotes, profile summary and the draft; 0/1/2 / Enter / s / q; appends to
   `evals/data/retrieval_labels_reviewed.jsonl` (status `human_reviewed`, timestamp); never writes the drafts.
   Note for review: some draft reasons claim facts the posting does not state (e.g. "batch 2027 eligible" for the
   HackerRank SDE intern, where no batch was extracted) — the tool shows the evidence next to the draft.

## Session 9 (2026-11-19) — review-tool investigation (no label changes)

- **Pairs in pool v4 whose job has no job_facts row: 0.** All 57 distinct jobs (134 pairs) have facts
  (113 on facts_v3, the pasted ones on facts_v4). Coinbase "Machine Learning Engineer" is job 693, facts_v3.
- **Root cause of "no extracted facts available":** `.env` has `DATABASE_URL=…@localhost:5432/joblens` (the system
  Postgres 17), not the dev DB (127.0.0.1:54329). The tool's lookup failed (password authentication failed for
  "joblens") and it silently fell back to the short pool summary — for every pair in that session.
- **Coinbase p15 draft ("Requires 2+ years experience") is supported by the posting:** "2+ years of professional
  experience in machine learning and software engineering, with experience shipping production-grade ML services."
  (under "Required Skills and Experience"). The drafter saw the pool summary, whose "Eligibility: 2+ years
  experience" line came from that extracted quote. The posting also says "IC4" (a level signal the seniority rule
  does not read from the posting text).
- **What the drafter sees:** for every pair, `title | company | location` + the first 420 characters of the pool
  summary (structured text built from the job's facts + the first 400 characters of the posting). No pair was
  drafted without facts, so no draft was marked `low_confidence`.
- **Review tool fixed:** reports which DB it reached and **stops** if job facts/text cannot be read (unless
  `--allow-no-db`); warns "NO EXTRACTED FACTS" and shows the first 1,500 characters of the raw posting when facts
  are missing; shows every posting line mentioning years/experience for every pair; records `shown` (facts /
  raw_posting / summary_only) in each reviewed row. 4 tests.
- **Extraction proposal:** not needed — no in-pool job is unextracted (0 Gemini calls).
- **Your 24 reviewed p00 labels** all point to jobs that have facts, but all 24 were made on the summary-only view
  (DB unreachable). Worth re-checking with the fixed tool; 2 differ from the draft (Sarvam Embedded Data Scientist
  1 → 2, Zscaler Java 1 → 0).

## Session 10 (2026-11-19) — human review, agreement, first E1 numbers

1. `evals/data/retrieval_labels_reviewed.jsonl` committed: **49 human-reviewed pairs** (p00 24 + sample 25:
   p15 8, p10 7, p09 5, p05 5), all reviewed with extracted facts shown. The stale
   `retrieval_labels_reviewed_v1_summary_only.jsonl` is gitignored and kept local.
2. **Agreement with the LLM drafts** (`python -m evals.agreement`, `evals/results/retrieval_agreement.md`):
   all 46/49 exact (49/49 within ±1); p00 23/24; sample 23/25. Changed: Zscaler Java SDE (p00) 1 → 0, Stripe New Grad
   (p09) 1 → 2, HackerRank AI Researcher Intern (p15) 1 → 0. Human reasons were not recorded by the tool. The
   draft was visible while labeling, so agreement is likely inflated by anchoring.
3. **E1 retrieval** (`python -m evals.e1`, `evals/results/retrieval_e1.{json,md}`; pool v4; k = 10).
   Assumption: retrieved jobs without a label count as not relevant. **Small sample, noisy — indicative only.**

   (a1) p00 — the only profile whose top-10 is fully human-labeled (no assumption used):

   | Method | P@5 | P@10 | nDCG@10 | MRR |
   |---|---|---|---|---|
   | keyword | 0.60 | 0.60 | 0.345 | 0.333 |
   | vector (minilm, structured) | 0.80 | 0.70 | 0.783 | 1.000 |
   | hybrid RRF | 0.40 | 0.70 | 0.585 | 1.000 |

   (a) human-reviewed only, 5 profiles (labels: p00 24, p05 5, p09 5, p10 7, p15 8): keyword nDCG@10 0.272,
   vector 0.273, hybrid 0.266 — dominated by the assumption (30–33 of 50 top-10 slots unlabeled).
   (b) **includes LLM drafts** (134 pairs: 49 human + 85 qwen drafts, not hand-labeled): P@5 keyword 0.56 /
   vector 0.52 / hybrid 0.44; nDCG@10 0.446 / 0.468 / 0.463; MRR 0.267 / 0.350 / 0.383.
4. **Batch-year check (proposal only, nothing re-extracted):** 4 in-scope jobs have a batch year; 3 are genuine
   (Canonical "2026 and 2027 Graduate…", Rubrik "graduating in 2027/28", Rubrik "2027 graduates of …"); 1 is a
   degree-completion deadline read as a batch: Stripe New Grad (#1270) "obtained by summer 2027" → [2027], which
   makes 2025/2026 graduates INELIGIBLE (hidden). No in-scope batch currently comes from internship/joining dates.
   Proposed: (1) prompt rule (next prompt version): a deadline such as "obtained by summer 2027", "complete your
   degree before …", "no later than …" is not a batch list → graduation_years null; (2) code guard, no LLM: after
   extraction, null graduation_years when its evidence matches deadline wording (obtained by / by <season|month> /
   complete(d) by|before / no later than / prior to start) or a job/internship date without a batch word, and log it.
   With the current schema (a list of years) "≤ 2027" cannot be stored, so the honest result is null → eligibility
   UNKNOWN (CHECK), not FAIL. Effect today: 1 job (Stripe #1270).

## Session 11 (2026-11-19)

1. **Batch-year guard** (code, no LLM; `guard_batch_years` in `app/extract/facts.py`): after extraction,
   graduation_years → null when its quote is degree-deadline wording ("obtained by", "complete … by/before",
   "no later than", "by summer") or a job/internship date without a batch word. Applied to stored in-scope facts:
   4 checked, **1 changed — Stripe #1270** ("obtained by summer 2027"). p09 (2025 graduate) for Stripe:
   INELIGIBLE → CHECK. `facts_v5` = facts_v4 + the deadline rule, set as `prompts.facts_paste` (pasted/future
   only; board jobs stay on facts_v3, nothing re-extracted; the 6 pasted jobs keep facts_v4 facts until a
   `--stale` run, 6 Gemini calls).
2. **p00 hybrid vs vector (diagnosis only):** top-10 label sums vector 13, hybrid 10, keyword 7. Causes: the
   keyword query ORs 12 broad skill terms, so ts_rank_cd ranks long postings with many skill words first (keyword
   #1/#2 are both human 0), and RRF gives keyword rank 1 the same weight as vector rank 1 (100ms "AI Engineer", label
   0, keyword 1 / vector 8 → hybrid #2); 3 of the 7 relevance-2 jobs never appear in keyword's top 50, so they get one
   RRF vote and drop out of hybrid's top 10. **E1 bug found:** the runner fused 10 candidates per list, production
   fuses 50, so E1's hybrid row did not measure the production hybrid. What-if on p00 (depth 50): nDCG@10 hybrid
   0.628 (keyword weight 1.0), 0.646 (0.5), 0.607 (0.25) vs vector 0.783 — no clear weight fix. Proposed (not
   applied): fix the E1 depth; review the keyword query (role terms to match, skills to boost — a spec deviation)
   or make the matches mode configurable with vector as default until more profiles are labeled.
3. **P5 trust** (`app/trust/{rules,ghost,classifier,score}.py`, `ml/train_scam_classifier.py`, pipeline step
   "trust", 10 tests). Rules: money request, WhatsApp/Telegram-only contact, free-email recruiter, unrealistic pay,
   missing company/URL; ghost: age > 60 / > 120 days, reposts; classifier delta −round(40 × p). Only rules can mark
   scam signals. **Classifier** (Kaggle EMSCAD 2012–2014, 17,880 rows, 4.8% fraud, 60% US / 13% GB / 1.5% IN) on the
   held-out test split (1,788 rows, 87 fraud), threshold 0.51 chosen on validation: **precision 0.959, recall 0.816,
   F1 0.882, PR-AUC 0.907, recall@precision≥0.8 0.851. Not validated on our Indian/ATS postings.** Model file
   `models/scam_clf.joblib` is local (gitignored); report in `evals/results/trust_classifier.md`.
   Trust on 119 in-scope jobs: 118 ok, 1 caution (100ms "Benefit Verification Specialist", posted 447 days ago);
   board scam_prob median 0.001, max 0.052; no scam rule fired on any real job. Pasted jobs: Elip 90 ok,
   SpectoVX 89 ok, JNJ pieces 86–88 ok — only "missing company" (no URL; no company name for the JNJ pieces) and
   small classifier deltas. Synthetic scam drafts (written by Claude, in memory only): rules flagged both
   scam-style posts; the classifier gave the Indian "training fee + guaranteed placement" post only 0.07.
   E6 (30 hand-collected Indian examples) is still pending.

## Session 12 (2026-11-19)

1. **E1 depth bug fixed**: E1 now fuses at production depth (50 candidates per list) before cutting to top-10.
   Updated (small, noisy; unlabeled = not relevant):

   | Set | Method | P@5 | nDCG@10 | MRR |
   |---|---|---|---|---|
   | p00 human (24 pairs) | keyword | 0.60 | 0.345 | 0.333 |
   | | vector | 0.80 | 0.783 | 1.000 |
   | | hybrid | 0.80 | 0.628 | 1.000 (1/10 unlabeled) |
   | human-only, 5 profiles (49 pairs) | keyword | 0.20 | 0.272 | 0.142 |
   | | vector | 0.20 | 0.273 | 0.200 |
   | | hybrid | 0.28 | 0.372 | 0.273 |
   | **includes LLM drafts** (134 pairs) | keyword | 0.56 | 0.446 | 0.267 |
   | | vector | 0.52 | 0.468 | 0.350 |
   | | hybrid | 0.68 | 0.519 | 0.423 |
2. **Keyword query experiment** (`python -m evals.keyword_experiment`, not production): role-words match + skill
   boost vs current. With-drafts set: keyword nDCG@10 0.446 → 0.369, hybrid 0.519 → 0.422 (MRR 0.423 → 0.375);
   p00 sanity check agrees. Biased toward the current query (the labeled pool came from current methods;
   role_boost puts 16/50 unlabeled jobs in keyword top-10). Not clearly better → **production unchanged**,
   `/matches` default unchanged.
3. **JNJ re-paste**: the file now starts with "Company: Johnson & Johnson". Old pieces 1298–1301 marked inactive;
   new pieces 1302–1305 (facts_v5), company set: JJT Internship (internship, ₹75k/month), TLDP (full_time,
   ₹15,65,000/yr), Product Engineer (type not stated, ₹15,52,000/yr), "needs human title" (internship, ₹75k/month,
   degrees MASTERS + MCA). Branches for all: computer science, electronics, electrical engineering, mathematics and
   computing ("ee" and "mnc" now mapped). Your eligibility: CHECK for all (batch unknown, Hyderabad soft-fail).
4. **Trust**: pasted jobs no longer get the "no job URL" penalty (company name still checked). Elip 90 → 100,
   SpectoVX 89 → 99, JNJ pieces 87–89 → 97–99; all ok.
5. **Role-family check (proposal only):** 100ms "Benefit Verification Specialist - AI Agents (Healthcare)" is data_ml
   because the rule matches "ai" anywhere in the title (here a team/product suffix); its required skills are
   non-technical (insurance verification, payer portals, CPT/HCPCS). Of 113 in-scope board jobs, 28 titles contain a
   non-tech-looking word: ~6 clearly non-tech, ~16 borderline (IT, support, consulting), ~6 fine. Prototype (in
   memory): classify on the title head (before " - ", "(", ":"), reject hard non-tech words (sales, revenue,
   partnerships, content, risk management, verification, benefits, recruiting, people operations) unless the head
   is engineer/developer/SDE/scientist, and veto weak title words (ai, platform, systems, security, embedded, cloud)
   when the extracted skills contain no tech skill. It would move 10 out (in scope 113 → 103 board + 6 pasted);
   debatable moves: CloudSEK "Security Consultant Intern" and "Technical Security Consultant", Stable Money "AI
   Product Builder" (no tech skills extracted); it misses "Training Content Developer". Not applied.

## Not started: P6, P7.
