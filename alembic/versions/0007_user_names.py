"""accounts gain a person's name: first_name, last_name

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-16

Until now an account was only a credential. `username` is what you log in with and is
global, and that was enough for every screen inside the system — but the PDF report is
read *outside* it, where «a.karimi» in the masthead identifies nobody. So the row now
carries the person as well as the credential, and `app/reports/render.py` prints
`user.display_name`.

**Both columns are nullable**, the same bargain migration 0006 made for the organization
profile: every account that exists when this runs has no name, and a NOT NULL would have
meant inventing one for each of them. Being required is the *schema's* job — `AccountIn`
asks for both, so no new account can be nameless — and `POST /accounts/{id}/name` (an
admin) and `POST /auth/name` (the account itself, which is how the seeded first
super_admin gets one) are how the rows that predate this are filled in. Until they are,
`display_name` falls back to the username, so nothing prints blank.

Not unique and not indexed: two people may share a name, and nothing looks an account up
by it — `username` is still the identifier.
"""
import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

COLUMNS = (
    sa.Column("first_name", sa.String(64), nullable=True),
    sa.Column("last_name", sa.String(64), nullable=True),
)


def upgrade() -> None:
    for column in COLUMNS:
        op.add_column("users", column)


def downgrade() -> None:
    for column in reversed(COLUMNS):
        op.drop_column("users", column.name)
