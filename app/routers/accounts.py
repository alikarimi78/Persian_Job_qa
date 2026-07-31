"""Account creation, one endpoint per role.

The chain is deliberate — each level provisions the one below it, and a super_admin may
stand in anywhere:

    POST /accounts/super-admins   super_admin
    POST /accounts/org-admins     super_admin                       (one per organization)
    POST /accounts/unit-admins    super_admin | org_admin           (one per unit)
    POST /accounts/users          super_admin | unit_admin

An org_admin deliberately cannot create ordinary users: they create the units and the
unit admins, and those admins staff their own unit.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..accounts import (assert_can_manage_account, assert_manages_organization,
                        assert_manages_unit, create_account, get_account, get_organization,
                        get_unit, set_active, set_password, visible_users)
from ..auth import require_roles, require_super_admin
from ..database import get_db
from ..models import Role, Unit, User
from ..schemas import (AccountIn, OrgAdminIn, PasswordResetIn, UnitAdminIn, UserAccountIn,
                       UserOut)

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.post("/super-admins", response_model=UserOut, status_code=201,
             dependencies=[Depends(require_super_admin)])
def create_super_admin(body: AccountIn, db: Session = Depends(get_db)):
    """A super_admin is made only by another super_admin; the first one comes from
    ADMIN_USERNAME/ADMIN_PASSWORD in `scripts/seed_from_xlsx.py`."""
    return create_account(db, username=body.username, password=body.password,
                          role=Role.super_admin)


@router.post("/org-admins", response_model=UserOut, status_code=201,
             dependencies=[Depends(require_super_admin)])
def create_org_admin(body: OrgAdminIn, db: Session = Depends(get_db)):
    get_organization(db, body.organization_id)
    return create_account(db, username=body.username, password=body.password,
                          role=Role.org_admin, organization_id=body.organization_id)


@router.post("/unit-admins", response_model=UserOut, status_code=201)
def create_unit_admin(body: UnitAdminIn,
                      actor: User = Depends(require_roles(Role.super_admin, Role.org_admin)),
                      db: Session = Depends(get_db)):
    unit = get_unit(db, body.unit_id)
    assert_manages_organization(actor, unit.organization_id)
    return create_account(db, username=body.username, password=body.password,
                          role=Role.unit_admin, unit_id=unit.id)


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserAccountIn,
                actor: User = Depends(require_roles(Role.super_admin, Role.unit_admin)),
                db: Session = Depends(get_db)):
    unit_id = body.unit_id
    if actor.role is Role.unit_admin:
        if unit_id is not None and unit_id != actor.unit_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Outside your unit")
        unit_id = actor.unit_id
    elif unit_id is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "unit_id is required")

    unit = get_unit(db, unit_id)
    assert_manages_unit(actor, unit)
    return create_account(db, username=body.username, password=body.password,
                          role=Role.user, unit_id=unit.id)


_any_admin = require_roles(Role.super_admin, Role.org_admin, Role.unit_admin)


@router.post("/{user_id}/block", response_model=UserOut)
def block_account(user_id: int, actor: User = Depends(_any_admin),
                  db: Session = Depends(get_db)):
    """Refuses the account entry without deleting anything: its suggestions, its unit
    and its history stay. Blocking is not inherited — blocking a unit_admin leaves that
    unit's users able to log in."""
    target = get_account(db, user_id)
    assert_can_manage_account(actor, target)
    return set_active(db, target, False)


@router.post("/{user_id}/unblock", response_model=UserOut)
def unblock_account(user_id: int, actor: User = Depends(_any_admin),
                    db: Session = Depends(get_db)):
    target = get_account(db, user_id)
    assert_can_manage_account(actor, target)
    return set_active(db, target, True)


@router.post("/{user_id}/password", response_model=UserOut)
def reset_password(user_id: int, body: PasswordResetIn, actor: User = Depends(_any_admin),
                   db: Session = Depends(get_db)):
    """Sets a new password for an account below the caller. The old one is not asked
    for: this exists precisely for the account that cannot supply it."""
    target = get_account(db, user_id)
    assert_can_manage_account(actor, target)
    return set_password(db, target, body.password)


@router.get("", response_model=list[UserOut])
def list_accounts(role: Role | None = None, unit_id: int | None = None,
                  organization_id: int | None = None,
                  actor: User = Depends(_any_admin), db: Session = Depends(get_db)):
    """The accounts within the caller's span of control, optionally filtered."""
    query = visible_users(db, actor)
    if role is not None:
        query = query.filter(User.role == role)
    if unit_id is not None:
        query = query.filter(User.unit_id == unit_id)
    if organization_id is not None:
        # Matches an org_admin by their own column and everyone else through their unit
        unit_ids = db.query(Unit.id).filter(Unit.organization_id == organization_id)
        query = query.filter((User.organization_id == organization_id)
                             | (User.unit_id.in_(unit_ids)))
    return query.order_by(User.username).all()
