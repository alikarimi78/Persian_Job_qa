"""initial schema: users + jobs_info

Revision ID: 0001
Revises:
Create Date: 2026-07-06
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

role_enum = sa.Enum("user", "admin", name="role")
status_enum = sa.Enum("approved", "pending", "rejected", name="jobstatus")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("hashed_password", sa.String(128), nullable=False),
        sa.Column("role", role_enum, nullable=False, server_default="user"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    op.create_table(
        "jobs_info",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("job_title", sa.String(255), nullable=False),
        sa.Column("aliases", sa.Text, nullable=False, server_default=""),
        sa.Column("tools", sa.Text, nullable=False, server_default=""),
        sa.Column("skills", sa.Text, nullable=False, server_default=""),
        sa.Column("work_context", sa.Text, nullable=False, server_default=""),
        sa.Column("career_path_next", sa.Text, nullable=False, server_default=""),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("responsibilities", sa.Text, nullable=False, server_default=""),
        sa.Column("status", status_enum, nullable=False, server_default="pending"),
        sa.Column("suggested_by", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reviewed_by", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_jobs_info_job_title", "jobs_info", ["job_title"])
    op.create_index("ix_jobs_info_status", "jobs_info", ["status"])


def downgrade() -> None:
    op.drop_table("jobs_info")
    op.drop_table("users")
    status_enum.drop(op.get_bind(), checkfirst=True)
    role_enum.drop(op.get_bind(), checkfirst=True)
