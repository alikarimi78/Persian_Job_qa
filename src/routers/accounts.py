"""Account creation, one endpoint per role.

The chain is deliberate — each level provisions the one below it, and a super_admin may
stand in anywhere:

    POST /accounts/super-admins   super_admin
    POST /accounts/org-admins     super_admin                       (one per organization)
    POST /accounts/users          super_admin | org_admin

An org_admin staffs its own organization and nothing else: it cannot make another
org_admin, and a super_admin is the only one who decides an organization exists at all.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from prisma import Prisma
from prisma.types import UserWhereInput

from ..accounts import (assert_can_manage_account, assert_manages_organization,
                        create_account, delete_account, get_account, get_organization,
                        move_to_organization, set_active, set_name, set_password,
                        visible_users)
from ..auth import require_roles, require_super_admin
from ..database import get_db
from ..models import Role, User
from ..schemas import (AccountIn, MoveOrganizationIn, NameIn, OrgAdminIn,
                       PasswordResetIn, UserAccountIn, UserOut)

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.post("/super-admins", response_model=UserOut, status_code=201,
             dependencies=[Depends(require_super_admin)])
def create_super_admin(body: AccountIn, db: Prisma = Depends(get_db)):
    """A super_admin is made only by another super_admin; the first one comes from
    ADMIN_USERNAME/ADMIN_PASSWORD in `scripts/seed_from_xlsx.py`."""
    return create_account(db, username=body.username, password=body.password,
                          first_name=body.first_name, last_name=body.last_name,
                          role=Role.super_admin)


@router.post("/org-admins", response_model=UserOut, status_code=201,
             dependencies=[Depends(require_super_admin)])
def create_org_admin(body: OrgAdminIn, db: Prisma = Depends(get_db)):
    get_organization(db, body.organization_id)
    return create_account(db, username=body.username, password=body.password,
                          first_name=body.first_name, last_name=body.last_name,
                          role=Role.org_admin, organization_id=body.organization_id)


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserAccountIn,
                actor: User = Depends(require_roles(Role.super_admin, Role.org_admin)),
                db: Prisma = Depends(get_db)):
    """An ordinary user, in an organization. An org_admin's organization is not theirs
    to choose — naming a different one is a scope error rather than a silent redirect to
    their own — while a super_admin has none to default to and must say which."""
    organization_id = body.organization_id
    if actor.role == Role.org_admin:
        if organization_id is not None and organization_id != actor.organization_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Outside your organization")
        organization_id = actor.organization_id
    elif organization_id is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "organization_id is required")

    get_organization(db, organization_id)
    assert_manages_organization(actor, organization_id)
    return create_account(db, username=body.username, password=body.password,
                          first_name=body.first_name, last_name=body.last_name,
                          role=Role.user, organization_id=organization_id)


_any_admin = require_roles(Role.super_admin, Role.org_admin)


@router.post("/{user_id}/block", response_model=UserOut)
def block_account(user_id: int, actor: User = Depends(_any_admin),
                  db: Prisma = Depends(get_db)):
    """Refuses the account entry without deleting anything: its suggestions, its
    organization and its history stay. Blocking is not inherited — blocking an org_admin
    leaves that organization's users able to log in."""
    target = get_account(db, user_id)
    assert_can_manage_account(actor, target)
    return set_active(db, target, False)


@router.post("/{user_id}/unblock", response_model=UserOut)
def unblock_account(user_id: int, actor: User = Depends(_any_admin),
                    db: Prisma = Depends(get_db)):
    target = get_account(db, user_id)
    assert_can_manage_account(actor, target)
    return set_active(db, target, True)


@router.post("/{user_id}/password", response_model=UserOut)
def reset_password(user_id: int, body: PasswordResetIn, actor: User = Depends(_any_admin),
                   db: Prisma = Depends(get_db)):
    """Sets a new password for an account below the caller. The old one is not asked
    for: this exists precisely for the account that cannot supply it."""
    target = get_account(db, user_id)
    assert_can_manage_account(actor, target)
    return set_password(db, target, body.password)


@router.post("/{user_id}/name", response_model=UserOut)
def rename_account(user_id: int, body: NameIn, actor: User = Depends(_any_admin),
                   db: Prisma = Depends(get_db)):
    """Corrects the *person's* name on an account below the caller — a typo, a changed
    surname, or the name an account created before migration 0007 never had.

    Not a rename of the account itself: `username` is the credential and stays what it
    is, so nothing about logging in or about who owns which suggestion changes here.
    Same authority as every other action on somebody else's account, and the same rule
    that nobody may act on their own — `POST /auth/name` is that one.
    """
    target = get_account(db, user_id)
    assert_can_manage_account(actor, target)
    return set_name(db, target, body.first_name, body.last_name)


@router.post("/{user_id}/organization", response_model=UserOut)
def move_account_organization(user_id: int, body: MoveOrganizationIn,
                              actor: User = Depends(require_super_admin),
                              db: Prisma = Depends(get_db)):
    """Moves an account into another organization, keeping its role — an org_admin or an
    ordinary user, since both sit in an organization directly. It is the whole of what
    «ویرایش» means beyond the person's name, since a role decides which scope column the
    row carries and a username is the credential.

    Super-admin only, and not by omission: an org_admin has no second organization to
    move anyone into, so the endpoint would have nothing to offer them.
    `assert_can_manage_account` is still asked, because it is also what stops a caller
    acting on their own row.
    """
    target = get_account(db, user_id)
    assert_can_manage_account(actor, target)
    organization = get_organization(db, body.organization_id)
    return move_to_organization(db, target, organization)


@router.delete("/{user_id}", status_code=204)
def delete_account_endpoint(user_id: int, actor: User = Depends(_any_admin),
                            db: Prisma = Depends(get_db)):
    """Deletes an account below the caller. Anything it suggested stays in the corpus,
    unattributed. Blocking is the reversible option; this one is not."""
    target = get_account(db, user_id)
    assert_can_manage_account(actor, target)
    delete_account(db, target)


@router.get("", response_model=list[UserOut])
def list_accounts(role: Role | None = None, organization_id: int | None = None,
                  actor: User = Depends(_any_admin), db: Prisma = Depends(get_db)):
    """The accounts within the caller's span of control, optionally filtered.

    Built as one `where` clause rather than as the chain of `.filter()` calls SQLAlchemy
    allowed: `visible_users` puts the caller's scope and this narrowing under an `AND`,
    so a filter can only ever subtract from what the caller was already entitled to see.
    """
    filters: list[UserWhereInput] = []
    if role is not None:
        filters.append({"role": role})
    if organization_id is not None:
        filters.append({"organization_id": organization_id})
    where: UserWhereInput | None = {"AND": filters} if filters else None
    return visible_users(db, actor, where, order="username")
