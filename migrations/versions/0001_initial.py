"""Initial schema (spec Section 8).

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE jobs (
  id              BIGSERIAL PRIMARY KEY,
  source          TEXT NOT NULL,
  source_job_id   TEXT NOT NULL,
  company         TEXT NOT NULL,
  title           TEXT NOT NULL,
  location_raw    TEXT,
  url             TEXT,
  description_text TEXT NOT NULL,
  content_hash    TEXT NOT NULL,
  posted_at       TIMESTAMPTZ,
  first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  is_active       BOOLEAN NOT NULL DEFAULT true,
  repost_count    INT NOT NULL DEFAULT 0,
  search_text     TEXT,
  fts             TSVECTOR GENERATED ALWAYS AS
                    (to_tsvector('english', coalesce(search_text, title || ' ' || description_text))) STORED,
  UNIQUE (source, source_job_id)
);
CREATE INDEX jobs_fts_idx ON jobs USING GIN (fts);
CREATE INDEX jobs_active_idx ON jobs (is_active);
CREATE TABLE job_facts (
  job_id          BIGINT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
  facts           JSONB NOT NULL,
  model           TEXT NOT NULL,
  prompt_version  TEXT NOT NULL,
  valid           BOOLEAN NOT NULL,
  extracted_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE job_embeddings (
  job_id          BIGINT REFERENCES jobs(id) ON DELETE CASCADE,
  model_name      TEXT NOT NULL,
  text_variant    TEXT NOT NULL,
  embedding       VECTOR(384) NOT NULL,
  PRIMARY KEY (job_id, model_name, text_variant)
);
CREATE INDEX job_emb_hnsw ON job_embeddings USING hnsw (embedding vector_cosine_ops);
CREATE TABLE trust_scores (
  job_id          BIGINT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
  score           INT NOT NULL,
  level           TEXT NOT NULL,
  reasons         JSONB NOT NULL,
  scam_prob       REAL,
  computed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE profiles (
  id              UUID PRIMARY KEY,
  data            JSONB NOT NULL,
  resume_text     TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE explanations (
  profile_id UUID REFERENCES profiles(id) ON DELETE CASCADE,
  job_id     BIGINT REFERENCES jobs(id) ON DELETE CASCADE,
  data       JSONB NOT NULL, model TEXT NOT NULL, prompt_version TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (profile_id, job_id)
);
CREATE TABLE chat_sessions (id UUID PRIMARY KEY, profile_id UUID REFERENCES profiles(id) ON DELETE CASCADE,
  summary TEXT, created_at TIMESTAMPTZ DEFAULT now());
CREATE TABLE chat_messages (id BIGSERIAL PRIMARY KEY, session_id UUID REFERENCES chat_sessions(id) ON DELETE CASCADE,
  role TEXT NOT NULL, content JSONB NOT NULL, created_at TIMESTAMPTZ DEFAULT now());
CREATE TABLE user_memory (profile_id UUID REFERENCES profiles(id) ON DELETE CASCADE,
  key TEXT NOT NULL, value JSONB NOT NULL, updated_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (profile_id, key));
CREATE TABLE llm_cache (key TEXT PRIMARY KEY, model TEXT, prompt_version TEXT,
  response JSONB NOT NULL, tokens_in INT, tokens_out INT, created_at TIMESTAMPTZ DEFAULT now());
CREATE TABLE eval_runs (id BIGSERIAL PRIMARY KEY, suite TEXT NOT NULL, config JSONB NOT NULL,
  metrics JSONB NOT NULL, git_commit TEXT, created_at TIMESTAMPTZ DEFAULT now());
"""

DROP = """
DROP TABLE IF EXISTS eval_runs, llm_cache, user_memory, chat_messages, chat_sessions, explanations,
  profiles, trust_scores, job_embeddings, job_facts, jobs CASCADE;
"""


def upgrade() -> None:
    op.execute(SCHEMA)


def downgrade() -> None:
    op.execute(DROP)
