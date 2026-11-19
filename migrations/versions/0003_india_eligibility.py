"""Add jobs.is_india_eligible + location_reason and the generated jobs.in_scope flag.

in_scope = is_fresher_relevant AND is_india_eligible; matching, evals and extraction use it by default.
Remote listings limited to other countries ("Remote - US", "Vienna, Austria / Remote") are not India-eligible.

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE jobs ADD COLUMN is_india_eligible BOOLEAN NOT NULL DEFAULT true;
        ALTER TABLE jobs ADD COLUMN location_reason TEXT;
        ALTER TABLE jobs ADD COLUMN in_scope BOOLEAN GENERATED ALWAYS AS (is_fresher_relevant AND is_india_eligible) STORED;
        CREATE INDEX jobs_in_scope_idx ON jobs (is_active, in_scope);
    """)


def downgrade() -> None:
    op.execute("""
        DROP INDEX IF EXISTS jobs_in_scope_idx;
        ALTER TABLE jobs DROP COLUMN IF EXISTS in_scope;
        ALTER TABLE jobs DROP COLUMN IF EXISTS location_reason;
        ALTER TABLE jobs DROP COLUMN IF EXISTS is_india_eligible;
    """)
