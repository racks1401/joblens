"""usage_counts: per-day action counters for demo rate limits (per client session and for the whole app).

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "usage_counts",
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("action", sa.Text(), primary_key=True),
        sa.Column("key", sa.Text(), primary_key=True),  # client session id, or "*" for the whole app
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("usage_counts")
