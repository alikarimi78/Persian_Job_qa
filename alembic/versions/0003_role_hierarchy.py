"""role hierarchy: organizations, units, and the four roles

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-01

The two-value role enum ('user', 'admin') becomes four: user, unit_admin, org_admin,
super_admin. The old lone `admin` was the top of the system, so it becomes `super_admin`.

The enum is replaced rather than extended. Postgres can ALTER TYPE ... ADD VALUE, but
the new value cannot be *used* in the same transaction that adds it, and this migration
has to rewrite existing rows — so a fresh type plus a USING cast does both in one step.

Existing regular users keep unit_id NULL: they predate the hierarchy and there is no
unit to guess for them. The CHECK constraint below tolerates that for role='user' only;
every account created through the API from now on carries its scope.
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

NEW_ROLES = "'user', 'unit_admin', 'org_admin', 'super_admin'"
OLD_ROLES = "'user', 'admin'"

SCOPE_CHECK = (
    "(role = 'super_admin' AND organization_id IS NULL AND unit_id IS NULL) OR "
    "(role = 'org_admin'   AND organization_id IS NOT NULL AND unit_id IS NULL) OR "
    "(role = 'unit_admin'  AND organization_id IS NULL AND unit_id IS NOT NULL) OR "
    "(role = 'user'        AND organization_id IS NULL)"
)


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_organizations_name", "organizations", ["name"], unique=True)

    op.create_table(
        "units",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("organization_id", sa.Integer,
                  sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("organization_id", "name", name="uq_units_org_name"),
    )
    op.create_index("ix_units_name", "units", ["name"])
    op.create_index("ix_units_organization_id", "units", ["organization_id"])

    # 'admin' -> 'super_admin', done as part of the type swap so the new value is never
    # referenced by a statement in the transaction that created its type.
    op.execute(sa.text(f"CREATE TYPE role_new AS ENUM ({NEW_ROLES})"))
    op.execute(sa.text("ALTER TABLE users ALTER COLUMN role DROP DEFAULT"))
    op.execute(sa.text(
        "ALTER TABLE users ALTER COLUMN role TYPE role_new USING ("
        "  CASE role::text WHEN 'admin' THEN 'super_admin' ELSE role::text END::role_new)"))
    op.execute(sa.text("ALTER TABLE users ALTER COLUMN role SET DEFAULT 'user'"))
    op.execute(sa.text("DROP TYPE role"))
    op.execute(sa.text("ALTER TYPE role_new RENAME TO role"))

    op.add_column("users", sa.Column("organization_id", sa.Integer,
                                     sa.ForeignKey("organizations.id"), nullable=True))
    op.add_column("users", sa.Column("unit_id", sa.Integer,
                                     sa.ForeignKey("units.id"), nullable=True))
    op.create_index("ix_users_organization_id", "users", ["organization_id"])
    op.create_index("ix_users_unit_id", "users", ["unit_id"])

    op.create_check_constraint("ck_users_scope", "users", SCOPE_CHECK)
    # One admin per organization, one per unit; ordinary users share a unit freely.
    op.create_index("uq_users_org_admin", "users", ["organization_id"], unique=True,
                    postgresql_where=sa.text("role = 'org_admin'"))
    op.create_index("uq_users_unit_admin", "users", ["unit_id"], unique=True,
                    postgresql_where=sa.text("role = 'unit_admin'"))


def downgrade() -> None:
    # Everything below 'admin' collapses back: org and unit admins have no
    # representation in the two-value enum, so they return as ordinary users.
    op.drop_index("uq_users_unit_admin", table_name="users")
    op.drop_index("uq_users_org_admin", table_name="users")
    op.drop_constraint("ck_users_scope", "users", type_="check")
    op.drop_index("ix_users_unit_id", table_name="users")
    op.drop_index("ix_users_organization_id", table_name="users")
    op.drop_column("users", "unit_id")
    op.drop_column("users", "organization_id")

    op.execute(sa.text(f"CREATE TYPE role_old AS ENUM ({OLD_ROLES})"))
    op.execute(sa.text("ALTER TABLE users ALTER COLUMN role DROP DEFAULT"))
    op.execute(sa.text(
        "ALTER TABLE users ALTER COLUMN role TYPE role_old USING ("
        "  CASE role::text WHEN 'super_admin' THEN 'admin' ELSE 'user' END::role_old)"))
    op.execute(sa.text("ALTER TABLE users ALTER COLUMN role SET DEFAULT 'user'"))
    op.execute(sa.text("DROP TYPE role"))
    op.execute(sa.text("ALTER TYPE role_old RENAME TO role"))

    op.drop_table("units")
    op.drop_index("ix_organizations_name", table_name="organizations")
    op.drop_table("organizations")
