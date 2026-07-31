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
