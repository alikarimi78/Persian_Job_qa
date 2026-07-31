"""deleting an account keeps its job records: jobs_info FKs become ON DELETE SET NULL

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-01

`jobs_info.suggested_by` and `reviewed_by` pointed at `users.id` with no delete rule, so
deleting anyone who had ever suggested or reviewed a record failed on the foreign key.

SET NULL rather than CASCADE, deliberately: an approved record is part of the corpus the
whole system searches, and it must not disappear because the person who proposed it left.
The attribution goes, the record stays. Both columns were already nullable.

The constraint names are Postgres's own defaults from migration 0001, where the foreign
keys were declared inline without an explicit name.
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

FKS = (("jobs_info_suggested_by_fkey", "suggested_by"),
       ("jobs_info_reviewed_by_fkey", "reviewed_by"))


def upgrade() -> None:
    for name, column in FKS:
        op.drop_constraint(name, "jobs_info", type_="foreignkey")
        op.create_foreign_key(name, "jobs_info", "users", [column], ["id"],
                              ondelete="SET NULL")


def downgrade() -> None:
    for name, column in FKS:
        op.drop_constraint(name, "jobs_info", type_="foreignkey")
        op.create_foreign_key(name, "jobs_info", "users", [column], ["id"])
