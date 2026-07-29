"""add knowledge + abilities columns to jobs_info

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-30

Rows seeded before this migration keep the "" server default; they only gain real
content on a re-seed from a dataset that carries the two columns.
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs_info",
                  sa.Column("knowledge", sa.Text, nullable=False, server_default=""))
    op.add_column("jobs_info",
                  sa.Column("abilities", sa.Text, nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("jobs_info", "abilities")
    op.drop_column("jobs_info", "knowledge")
