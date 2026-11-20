"""jobs.expired: the application deadline has passed (set by app.ingest.expire; rows are kept).

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("expired", sa.Boolean(), server_default="false", nullable=False))


def downgrade() -> None:
    op.drop_column("jobs", "expired")
