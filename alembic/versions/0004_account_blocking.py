"""block/unblock accounts: users.is_active

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-01

Existing accounts are active, which is what the server_default gives them. A blocked
account keeps its row, its unit and everything it has suggested — only authentication
is refused, at login and on every request made with a token issued earlier.
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("is_active", sa.Boolean, nullable=False,
                                     server_default=sa.text("true")))


def downgrade() -> None:
    # Blocked accounts become ordinary ones again: the flag is the only record of it
    op.drop_column("users", "is_active")
