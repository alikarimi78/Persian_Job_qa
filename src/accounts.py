"""Account creation and the scope rules behind it.

Every account is made by the level above it, so the same two questions come up at each
level: may this caller touch this organization/unit, and is the account they are asking
for well-formed. Both live here so the routers stay thin and the rules are stated once.

Two things changed shape when Prisma replaced SQLAlchemy, and both are load-bearing:

- **The lookups say what they need loaded.** `get_account` includes `unit`, because
  `assert_can_manage_account` reaches through it to find the organization an ordinary
  account sits in. Prisma leaves an un-included relation as `None` rather than fetching
  it on access, so an org_admin would otherwise be told every account is outside their
  organization — a wrong answer, not an error.
- **`visible_users` is a filter, not a query object.** It used to return a SQLAlchemy
  `Query` for the caller to narrow further; there is no such handle here, so
  `visible_scope` gives back the `where` clause and callers combine it with `AND`.
"""

from fastapi import HTTPException, status
from prisma import Prisma
from prisma.types import UserWhereInput

from .auth import hash_password
from .models import OrganizationSummary, Role, Unit, User, scope_organization_id


def get_account(db: Prisma, user_id: int) -> User:
    """The account, with its unit loaded — `assert_can_manage_account` and
    `models.scope_organization_id` both read through it."""
    user = db.user.find_unique(where={"id": user_id}, include={"unit": True})
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")
    return user


def get_organization(db: Prisma, organization_id: int) -> OrganizationSummary:
    """Without the logo: every caller here wants the row, not the image. The one
    endpoint that wants the image asks for the full model itself
    (`routers/orgs.py:get_logo`)."""
    org = OrganizationSummary.prisma(db).find_unique(where={"id": organization_id})
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    return org


def get_unit(db: Prisma, unit_id: int) -> Unit:
    unit = db.unit.find_unique(where={"id": unit_id})
    if unit is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unit not found")
    return unit


def organization_of(db: Prisma, user: User) -> OrganizationSummary | None:
    """Where an account sits, resolved the way `GET /auth/me` reports it: an org_admin
    carries the id itself, everyone below reaches it through their unit.

    Fetched separately rather than by including `organization` on the account, because
    including it would select every column of the row — the logo blob among them — on
    requests that only want a name."""
    organization_id = scope_organization_id(user)
    if organization_id is None:
        return None
    return OrganizationSummary.prisma(db).find_unique(where={"id": organization_id})


def assert_manages_organization(actor: User, organization_id: int) -> None:
    """A super_admin manages every organization; an org_admin only their own."""
    if actor.role == Role.super_admin:
        return
    if actor.role == Role.org_admin and actor.organization_id == organization_id:
        return
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Outside your organization")


def assert_manages_unit(actor: User, unit: Unit) -> None:
    """A super_admin manages every unit, an org_admin the units of their organization,
    a unit_admin only their own unit."""
    if actor.role == Role.super_admin:
        return
    if actor.role == Role.org_admin and actor.organization_id == unit.organization_id:
        return
    if actor.role == Role.unit_admin and actor.unit_id == unit.id:
        return
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Outside your unit")


def assert_can_manage_account(actor: User, target: User) -> None:
    """Whether `actor` may block, unblock or re-password `target`.

    The rule is the provisioning chain read downwards: you may act on the accounts you
    could have created. An org_admin therefore reaches the unit admins and users of its
    own organization but not its peers, and a unit_admin reaches only the ordinary users
    of its unit.

    Nobody may act on their own account — an admin blocking themselves would need
    someone above them to undo it, and a unit_admin has nobody above them in their unit.

    `target.unit` has to have been loaded (`get_account` does it); an account fetched
    without it looks unit-less here, which reads as "outside your organization".
    """
    if actor.id == target.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You cannot act on your own account")
    if actor.role == Role.super_admin:
        return
    if actor.role == Role.org_admin:
        if target.unit is not None and target.unit.organization_id == actor.organization_id:
            return
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Outside your organization")
    if actor.role == Role.unit_admin:
        if target.role == Role.user and target.unit_id == actor.unit_id:
            return
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Outside your unit")
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role")


def delete_account(db: Prisma, target: User) -> None:
    """Removes the account for good. Its job suggestions survive it — the foreign keys
    are ON DELETE SET NULL (`prisma/migrations/0001_init`), so the records stay in the
    corpus and only lose their attribution.

    Nothing here can strand the system without a super_admin: deletion goes strictly
    downwards and nobody may delete themselves, so the last active super_admin — the one
    doing the deleting — is always still standing afterwards.
    """
    db.user.delete(where={"id": target.id})


def move_to_unit(db: Prisma, target: User, unit: Unit) -> User:
    """Moves an account to another unit, keeping its role.

    Only accounts that live in a unit can move: an org_admin belongs to an organization
    and a super_admin to none, so neither has anywhere to go. A unit_admin may move, but
    only into a unit that has no admin yet — checked here so the caller gets a 409
    naming the sitting admin rather than a Prisma error from the unique index.
    """
    if target.unit_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"A {target.role} does not belong to a unit")
    if target.role == Role.unit_admin and unit.id != target.unit_id:
        taken = db.user.find_first(where={"role": Role.unit_admin, "unit_id": unit.id})
        if taken:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"Unit already has an admin ({taken.username})")
    # `connect`, not a bare `unit_id`: Prisma's update input takes relations rather than
    # the foreign key column, which is the one asymmetry with `create` below.
    return db.user.update(where={"id": target.id},
                          data={"unit": {"connect": {"id": unit.id}}})


def move_to_organization(db: Prisma, target: User, organization: OrganizationSummary) -> User:
    """Moves an organization's admin to another organization, keeping its role.

    The counterpart of `move_to_unit` for the one role that does not sit in a unit.
    `ck_users_scope` gives a non-NULL organization_id to org_admin rows and to no other
    role, so the first check is also what refuses everybody else — a unit_admin or user
    moves through `/unit`, and a super_admin belongs nowhere.

    The second check turns the partial unique index into a 409 naming the admin already
    sitting there, the way creation does. It is skipped when the destination is the
    organization the account is already in, or re-submitting an unchanged form would be
    refused on account of the row itself.
    """
    if target.organization_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"A {target.role} does not belong to an organization")
    if organization.id != target.organization_id:
        taken = db.user.find_first(
            where={"role": Role.org_admin, "organization_id": organization.id})
        if taken:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"Organization already has an admin ({taken.username})")
    return db.user.update(where={"id": target.id},
                          data={"organization": {"connect": {"id": organization.id}}})


def set_active(db: Prisma, target: User, active: bool) -> User:
    return db.user.update(where={"id": target.id}, data={"is_active": active})


def set_password(db: Prisma, target: User, password: str) -> User:
    return db.user.update(where={"id": target.id},
                          data={"hashed_password": hash_password(password)})


def set_name(db: Prisma, target: User, first_name: str, last_name: str) -> User:
    """The one thing about an account that can be corrected without moving it. A username
    is the credential and never changes; a name is only what the person is called, which
    is why this needs no more authority than editing the account already takes."""
    return db.user.update(where={"id": target.id},
                          data={"first_name": first_name, "last_name": last_name})


def create_account(db: Prisma, *, username: str, password: str, role: Role,
                   first_name: str | None = None, last_name: str | None = None,
                   organization_id: int | None = None, unit_id: int | None = None) -> User:
    """Creates one account of any role, with the invariants the database also enforces.

    They are checked here as well as in the schema so the caller gets a 409 naming the
    conflict instead of a raw Prisma error: usernames are global (login is by username
    alone), and an organization or unit may hold only one admin.
    """
    if db.user.find_first(where={"username": username}):
        raise HTTPException(status.HTTP_409_CONFLICT, "Username already taken")

    if role == Role.org_admin:
        taken = db.user.find_first(
            where={"role": Role.org_admin, "organization_id": organization_id})
        if taken:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"Organization already has an admin ({taken.username})")
    elif role == Role.unit_admin:
        taken = db.user.find_first(where={"role": Role.unit_admin, "unit_id": unit_id})
        if taken:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"Unit already has an admin ({taken.username})")

    # The name is optional *here* and required by `AccountIn`: this helper is also what
    # `scripts/seed_from_xlsx.py`-shaped callers reach for, and the first super_admin is
    # created from two environment variables that carry no name. It fills its own in
    # through `POST /auth/name`.
    return db.user.create(data={
        "username": username,
        "hashed_password": hash_password(password),
        "role": role,
        "first_name": first_name,
        "last_name": last_name,
        "organization_id": organization_id,
        "unit_id": unit_id,
    })


def visible_scope(actor: User) -> UserWhereInput:
    """The `where` clause selecting the accounts an admin may see: everything for a
    super_admin, the organization's units for an org_admin, the unit's roster for a
    unit_admin.

    The org_admin branch is a relation filter rather than the `IN (SELECT …)` the
    SQLAlchemy version built by hand — same rows, one query. It also keeps the property
    that fell out of the subquery: an org_admin's own row has no `unit_id`, so it does
    not match, and an admin never appears in their own listing.
    """
    if actor.role == Role.super_admin:
        return {}
    if actor.role == Role.org_admin:
        return {"unit": {"is": {"organization_id": actor.organization_id}}}
    return {"unit_id": actor.unit_id}


def visible_users(db: Prisma, actor: User, where: UserWhereInput | None = None,
                  order: str | None = None) -> list[User]:
    """`visible_scope` applied, optionally narrowed further by the caller's own filter."""
    scope = visible_scope(actor)
    clause: UserWhereInput = scope if where is None else {"AND": [scope, where]}
    return db.user.find_many(where=clause,
                             order={"username": "asc"} if order == "username" else None)
