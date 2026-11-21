"""Copy the public job data from one JobLens database into a new, empty one (for example a fresh Neon database).

    SEED_SOURCE_URL=postgresql://...  SEED_TARGET_URL=postgresql://...  python scripts/seed_prod.py          # dry run
    SEED_SOURCE_URL=...  SEED_TARGET_URL=...  python scripts/seed_prod.py --apply                          # copy

The URLs are read from the environment, not from arguments, so they stay out of shell history and `ps` output.
The target is migrated to the latest schema first and must have no jobs. Only these rows are copied:

- jobs from the public job boards (`source <> 'paste'`: pasted postings are private),
- their extracted facts and trust scores,
- their embeddings for the default model only (MiniLM; the hosted demo serves no other model).

Profiles, chat, memory, explanations, the LLM cache, usage counters and eval runs are never copied. Every copy
uses an explicit column list, so generated columns (`in_scope`, `fts`) are rebuilt by the target.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

JOB_COLUMNS = [
    "id", "source", "source_job_id", "company", "title", "location_raw", "url", "description_text", "content_hash",
    "posted_at", "first_seen_at", "last_seen_at", "is_active", "expired", "repost_count", "search_text",
    "is_fresher_relevant", "seniority_reason", "is_india_eligible", "location_reason", "role_family", "role_reason",
]
FACT_COLUMNS = ["job_id", "facts", "model", "prompt_version", "valid", "extracted_at"]
TRUST_COLUMNS = ["job_id", "score", "level", "reasons", "scam_prob", "computed_at"]
EMBEDDING_COLUMNS = ["job_id", "model_name", "text_variant", "embedding"]

PUBLIC_JOBS = "SELECT id FROM jobs WHERE source <> 'paste'"


def plan(model: str) -> list[tuple[str, list[str], str]]:
    """(table, columns, WHERE clause) for each copy, in foreign-key order. COPY takes no bind parameters, so the
    model key is checked to be a plain name before it goes into the SQL."""
    if not re.fullmatch(r"[a-z0-9_-]+", model):
        raise SystemExit(f"unexpected embedding model key {model!r}")
    return [
        ("jobs", JOB_COLUMNS, "source <> 'paste'"),
        ("job_facts", FACT_COLUMNS, f"job_id IN ({PUBLIC_JOBS})"),
        ("trust_scores", TRUST_COLUMNS, f"job_id IN ({PUBLIC_JOBS})"),
        ("job_embeddings", EMBEDDING_COLUMNS, f"model_name = '{model}' AND job_id IN ({PUBLIC_JOBS})"),
    ]


def libpq_url(url: str) -> str:
    """psycopg wants postgresql://; the app's URLs use the SQLAlchemy form postgresql+psycopg://."""
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def describe(url: str) -> str:
    """host:port/db without the user or password, for printing."""
    u = urlsplit(libpq_url(url))
    return f"{u.hostname}:{u.port or 5432}{u.path}"


def migrate(target_url: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    sa = target_url if "+psycopg" in target_url else target_url.replace("postgresql://", "postgresql+psycopg://", 1)
    cfg.attributes["url"] = sa
    command.upgrade(cfg, "head")


def seed(source_url: str, target_url: str, model: str, apply: bool) -> dict[str, int]:
    """Copy the planned rows. Returns rows per table (the source counts on a dry run). Raises on any check failing."""
    import psycopg

    if describe(source_url) == describe(target_url):
        raise SystemExit("source and target are the same database")
    counts: dict[str, int] = {}
    with psycopg.connect(libpq_url(source_url)) as src:
        for table, _, where in plan(model):
            row = src.execute(f"SELECT count(*) FROM {table} WHERE {where}").fetchone()
            counts[table] = int(row[0]) if row else 0
        if not apply:
            return counts
        migrate(target_url)
        with psycopg.connect(libpq_url(target_url)) as dst:
            existing = dst.execute("SELECT count(*) FROM jobs").fetchone()
            if existing and existing[0]:
                raise SystemExit(f"target already has {existing[0]} jobs; seed only an empty database")
            with dst.transaction():
                for table, cols, where in plan(model):
                    col_list = ", ".join(cols)
                    q = f"COPY (SELECT {col_list} FROM {table} WHERE {where}) TO STDOUT"
                    with src.cursor().copy(q) as cin, dst.cursor().copy(
                            f"COPY {table} ({col_list}) FROM STDIN") as cout:
                        for chunk in cin:
                            cout.write(chunk)
                dst.execute("SELECT setval(pg_get_serial_sequence('jobs', 'id'), "
                            "COALESCE((SELECT max(id) FROM jobs), 1))")
            for table, _, _ in plan(model):
                row = dst.execute(f"SELECT count(*) FROM {table}").fetchone()
                got = int(row[0]) if row else 0
                if got != counts[table]:
                    raise SystemExit(f"{table}: copied {got} rows, expected {counts[table]}")
    return counts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="copy the rows (without it: dry run with counts only)")
    args = ap.parse_args()
    source, target = os.environ.get("SEED_SOURCE_URL", ""), os.environ.get("SEED_TARGET_URL", "")
    if not source or not target:
        raise SystemExit("set SEED_SOURCE_URL and SEED_TARGET_URL in the environment")
    from app.config import get_settings

    model = get_settings().cfg.embeddings.default_model
    print(f"source {describe(source)} -> target {describe(target)} (embeddings: {model} only)")
    counts = seed(source, target, model, args.apply)
    for table, n in counts.items():
        print(f"  {table}: {n} rows")
    print("copied and verified" if args.apply else "dry run: nothing written (add --apply to copy)")


if __name__ == "__main__":
    main()
