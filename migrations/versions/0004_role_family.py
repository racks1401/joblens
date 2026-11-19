"""Add jobs.role_family + role_reason; in_scope now also requires a tech family.

in_scope = is_fresher_relevant AND is_india_eligible AND role_family IN ('engineering', 'data_ml', 'product').
Matching, evals and drafting use in_scope by default; all jobs stay in the DB.

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE jobs ADD COLUMN role_family TEXT NOT NULL DEFAULT 'other';
        ALTER TABLE jobs ADD COLUMN role_reason TEXT;
        DROP INDEX IF EXISTS jobs_in_scope_idx;
        ALTER TABLE jobs DROP COLUMN in_scope;
        ALTER TABLE jobs ADD COLUMN in_scope BOOLEAN GENERATED ALWAYS AS
            (is_fresher_relevant AND is_india_eligible AND role_family IN ('engineering', 'data_ml', 'product')) STORED;
        CREATE INDEX jobs_in_scope_idx ON jobs (is_active, in_scope);
    """)


def downgrade() -> None:
    op.execute("""
        DROP INDEX IF EXISTS jobs_in_scope_idx;
        ALTER TABLE jobs DROP COLUMN in_scope;
        ALTER TABLE jobs ADD COLUMN in_scope BOOLEAN GENERATED ALWAYS AS (is_fresher_relevant AND is_india_eligible) STORED;
        CREATE INDEX jobs_in_scope_idx ON jobs (is_active, in_scope);
        ALTER TABLE jobs DROP COLUMN IF EXISTS role_reason;
        ALTER TABLE jobs DROP COLUMN IF EXISTS role_family;
    """)
