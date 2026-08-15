"""Account creation and the scope rules behind it.

Every account is made by the level above it, so the same two questions come up at each
level: may this caller touch this organization/unit, and is the account they are asking
for well-formed. Both live here so the routers stay thin and the rules are stated once.
"""

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from .auth import hash_password
from .models import Organization, Role, Unit, User


def get_account(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")
    return user


def get_organization(db: Session, organization_id: int) -> Organization:
    org = db.get(Organization, organization_id)
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    return org


def get_unit(db: Session, unit_id: int) -> Unit:
    unit = db.get(Unit, unit_id)
    if unit is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unit not found")
    return unit


def assert_manages_organization(actor: User, organization_id: int) -> None:
    """A super_admin manages every organization; an org_admin only their own."""
    if actor.role is Role.super_admin:
        return
    if actor.role is Role.org_admin and actor.organization_id == organization_id:
        return
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Outside your organization")


def assert_manages_unit(actor: User, unit: Unit) -> None:
    """A super_admin manages every unit, an org_admin the units of their organization,
    a unit_admin only their own unit."""
    if actor.role is Role.super_admin:
        return
    if actor.role is Role.org_admin and actor.organization_id == unit.organization_id:
        return
    if actor.role is Role.unit_admin and actor.unit_id == unit.id:
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
    """
    if actor.id == target.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You cannot act on your own account")
    if actor.role is Role.super_admin:
        return
    if actor.role is Role.org_admin:
        if target.unit is not None and target.unit.organization_id == actor.organization_id:
            return
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Outside your organization")
    if actor.role is Role.unit_admin:
        if target.role is Role.user and target.unit_id == actor.unit_id:
            return
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Outside your unit")
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role")


def delete_account(db: Session, target: User) -> None:
    """Removes the account for good. Its job suggestions survive it — the foreign keys
    are ON DELETE SET NULL (migration 0005), so the records stay in the corpus and only
    lose their attribution.

    Nothing here can strand the system without a super_admin: deletion goes strictly
    downwards and nobody may delete themselves, so the last active super_admin — the one
    doing the deleting — is always still standing afterwards.
    """
    db.delete(target)
    db.commit()


def move_to_unit(db: Session, target: User, unit: Unit) -> User:
    """Moves an account to another unit, keeping its role.

    Only accounts that live in a unit can move: an org_admin belongs to an organization
    and a super_admin to none, so neither has anywhere to go. A unit_admin may move, but
    only into a unit that has no admin yet — checked here so the caller gets a 409
    naming the sitting admin rather than an IntegrityError from the unique index.
    """
    if target.unit_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"A {target.role.value} does not belong to a unit")
    if target.role is Role.unit_admin and unit.id != target.unit_id:
        taken = db.query(User).filter(User.role == Role.unit_admin,
                                      User.unit_id == unit.id).first()
        if taken:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"Unit already has an admin ({taken.username})")
    target.unit_id = unit.id
    db.commit()
    db.refresh(target)
    return target


def move_to_organization(db: Session, target: User, organization: Organization) -> User:
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
                            f"A {target.role.value} does not belong to an organization")
    if organization.id != target.organization_id:
        taken = db.query(User).filter(User.role == Role.org_admin,
                                      User.organization_id == organization.id).first()
        if taken:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"Organization already has an admin ({taken.username})")
    target.organization_id = organization.id
    db.commit()
    db.refresh(target)
    return target


def set_active(db: Session, target: User, active: bool) -> User:
    target.is_active = active
    db.commit()
    db.refresh(target)
    return target


def set_password(db: Session, target: User, password: str) -> User:
    target.hashed_password = hash_password(password)
    db.commit()
    db.refresh(target)
    return target


def create_account(db: Session, *, username: str, password: str, role: Role,
                   organization_id: int | None = None, unit_id: int | None = None) -> User:
    """Creates one account of any role, with the invariants the database also enforces.

    They are checked here as well as in the schema so the caller gets a 409 naming the
    conflict instead of an IntegrityError: usernames are global (login is by username
    alone), and an organization or unit may hold only one admin.
    """
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Username already taken")

    if role is Role.org_admin:
        taken = db.query(User).filter(User.role == Role.org_admin,
                                      User.organization_id == organization_id).first()
        if taken:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"Organization already has an admin ({taken.username})")
    elif role is Role.unit_admin:
        taken = db.query(User).filter(User.role == Role.unit_admin,
                                      User.unit_id == unit_id).first()
        if taken:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"Unit already has an admin ({taken.username})")

    user = User(username=username, hashed_password=hash_password(password), role=role,
                organization_id=organization_id, unit_id=unit_id)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def visible_users(db: Session, actor: User):
    """The accounts an admin may see: everything for a super_admin, the organization's
    units for an org_admin, the unit's roster for a unit_admin."""
    query = db.query(User)
    if actor.role is Role.super_admin:
        return query
    if actor.role is Role.org_admin:
        unit_ids = db.query(Unit.id).filter(Unit.organization_id == actor.organization_id)
        return query.filter(User.unit_id.in_(unit_ids))
    return query.filter(User.unit_id == actor.unit_id)
