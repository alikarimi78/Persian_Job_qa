"""organizations gain a profile: code, address, phone, email and a logo

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-14

The customer's admin_panel.mp4 fills «افزودن سازمان» with more than a name — شناسه
سازمان, آدرس سازمان, شماره تماس, پست الکترونیکی and an «انتخاب لوگو» picker. These are
those five columns.

**All of them are nullable, including the four the reference form marks with a red
asterisk.** The requirement is the form's, not the column's: the organizations that
exist when this runs have none of this detail, and `PATCH /orgs/{id}` is how someone
fills it in — an admin fixing a typo in a name cannot be made to invent an email first.
A NOT NULL here would have needed a backfill of invented values, which is worse data
than an honest NULL.

`logo` is bytes in the row rather than a path on disk: the api container has no writable
volume beyond the two caches, and a file would be one more thing to back up in step with
the database. It is `deferred` in the model, so a list of organizations does not drag the
images along; `logo_mime` is the cheap column that answers whether there is one.
"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

COLUMNS = (
    sa.Column("code", sa.String(64), nullable=True),
    sa.Column("address", sa.String(512), nullable=True),
    sa.Column("phone", sa.String(32), nullable=True),
    sa.Column("email", sa.String(254), nullable=True),
    sa.Column("logo", sa.LargeBinary(), nullable=True),
    sa.Column("logo_mime", sa.String(64), nullable=True),
)


def upgrade() -> None:
    for column in COLUMNS:
        op.add_column("organizations", column)


def downgrade() -> None:
    for column in reversed(COLUMNS):
        op.drop_column("organizations", column.name)
