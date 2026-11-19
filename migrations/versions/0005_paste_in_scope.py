"""Pasted jobs are always in scope (they skip the seniority/location/role-family filters).

in_scope = source = 'paste' OR (is_fresher_relevant AND is_india_eligible AND role_family IN tech families)

Revision ID: 0005
Revises: 0004
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

TECH = "role_family IN ('engineering', 'data_ml', 'product')"


def _set(expr: str) -> None:
    op.execute(f"""
        DROP INDEX IF EXISTS jobs_in_scope_idx;
        ALTER TABLE jobs DROP COLUMN in_scope;
        ALTER TABLE jobs ADD COLUMN in_scope BOOLEAN GENERATED ALWAYS AS ({expr}) STORED;
        CREATE INDEX jobs_in_scope_idx ON jobs (is_active, in_scope);
    """)


def upgrade() -> None:
    _set(f"source = 'paste' OR (is_fresher_relevant AND is_india_eligible AND {TECH})")


def downgrade() -> None:
    _set(f"is_fresher_relevant AND is_india_eligible AND {TECH}")
