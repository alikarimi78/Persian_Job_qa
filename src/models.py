"""What is left of the models once Prisma owns the schema.

The tables are declared in `prisma/schema.prisma` and the classes are generated from it,
so this module is two things: the import site the rest of `src/` uses (nothing outside
here says `from prisma.models import …`, so a renamed model is one edit), and the home of
the three derived values that used to be `@property` on the SQLAlchemy classes.

They are functions now because a generated Prisma model is a pydantic model that is
rewritten by `prisma generate` — a property added to it would last exactly until the next
one. `display_name(user)` reads no worse than `user.display_name` and cannot be lost.

**Relations are None unless the query included them.** There is no lazy load to fall back
on — reading through a relation that was not included silently sees `None`, which is the
one class of bug this migration can introduce quietly. Nothing here reaches through a
relation any more: an account's organization is a column on the row itself.
"""

from prisma.enums import JobStatus, Role
from prisma.models import JobRecord, Organization, User
from prisma.partials import OrganizationSummary

__all__ = ["JobRecord", "JobStatus", "Organization", "OrganizationSummary", "Role",
           "User", "display_name", "full_name", "has_logo", "join_name",
           "scope_organization_id"]


def join_name(first_name: str | None, last_name: str | None) -> str | None:
    """«علی کریمی», or None when neither half is recorded.

    Takes the two halves rather than an account, because `schemas.UserOut` computes the
    same value from its own fields and the two must not drift — that type is what every
    account response goes through, and this is what the PDF's masthead goes through."""
    parts = [part for part in (first_name, last_name) if part]
    return " ".join(parts) if parts else None


def full_name(user: User) -> str | None:
    return join_name(user.first_name, user.last_name)


def display_name(user: User) -> str:
    """What a document should print for this account — the person's name, falling back
    to the username. The PDF report's masthead is the reason this exists: a report is
    read away from the system, where «a.karimi» identifies nobody, but a legacy account
    still has to print as *something*."""
    return full_name(user) or user.username


def has_logo(org: Organization | OrganizationSummary) -> bool:
    """What `OrganizationOut` reports in place of the image.

    Read off `logo_mime` and not off `logo`, which is what lets it be asked of an
    `OrganizationSummary` — the narrowed model that does not carry the blob at all, and
    the reason a list of organizations costs one small row each. The two columns are
    written and cleared together (`routers/orgs.py:apply_logo`), which is what makes the
    cheap one a truthful answer about the expensive one."""
    return org.logo_mime is not None


def scope_organization_id(user: User) -> int | None:
    """The organization this account belongs to. None for a super_admin (who belongs to
    all of them) and for a legacy user row that was never given one.

    A thin wrapper over the column now that the unit level is gone, kept because it is
    what every caller asks the question through — and because «which organization is this
    account in» is a question about the tenancy, not about a column."""
    return user.organization_id
