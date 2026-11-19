"""Add jobs.is_fresher_relevant + seniority_reason (rule-based seniority filter).

Not in spec Section 8: added so senior roles stay stored but are excluded from matching, evals and
extraction by default (JobLens targets freshers).

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE jobs ADD COLUMN is_fresher_relevant BOOLEAN NOT NULL DEFAULT true;
        ALTER TABLE jobs ADD COLUMN seniority_reason TEXT;
        CREATE INDEX jobs_fresher_idx ON jobs (is_active, is_fresher_relevant);
    """)


def downgrade() -> None:
    op.execute("""
        DROP INDEX IF EXISTS jobs_fresher_idx;
        ALTER TABLE jobs DROP COLUMN IF EXISTS seniority_reason;
        ALTER TABLE jobs DROP COLUMN IF EXISTS is_fresher_relevant;
    """)
