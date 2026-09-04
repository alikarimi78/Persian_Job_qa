from fastapi import APIRouter, Depends, HTTPException, status
from prisma import Prisma
from prisma.types import UserWhereInput

from src.database import get_db
from src.models import Role, User
from src.permissions import assert_can_manage_account, assert_manages_organization
from src.security import require_roles, require_super_admin
from src.routers.orgs.service import get_organization

from .schemas import (AccountIn, MoveOrganizationIn, NameIn, OrgAdminIn, PasswordResetIn,
                      UserAccountIn, UserOut)
from .service import (create_account, delete_account, get_account,
                      move_to_organization, set_active, set_name, set_password,
                      visible_users)

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.post("/super-admins", response_model=UserOut, status_code=201,
             dependencies=[Depends(require_super_admin)])
def create_super_admin(body: AccountIn, db: Prisma = Depends(get_db)):
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


# A super_admin may act at any level but must name the target organization explicitly.
@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserAccountIn,
                actor: User = Depends(require_roles(Role.super_admin, Role.org_admin)),
                db: Prisma = Depends(get_db)):
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
    target = get_account(db, user_id)
    assert_can_manage_account(actor, target)
    return set_active(db, target, False)


@router.post("/{user_id}/unblock", response_model=UserOut)
def unblock_account(user_id: int, actor: User = Depends(_any_admin),
                    db: Prisma = Depends(get_db)):
    target = get_account(db, user_id)
    assert_can_manage_account(actor, target)
    return set_active(db, target, True)


# An admin setting somebody else's password, which deliberately does not ask for the old
# one; `POST /auth/password` is the caller changing their own against the current one.
@router.post("/{user_id}/password", response_model=UserOut)
def reset_password(user_id: int, body: PasswordResetIn, actor: User = Depends(_any_admin),
                   db: Prisma = Depends(get_db)):
    target = get_account(db, user_id)
    assert_can_manage_account(actor, target)
    return set_password(db, target, body.password)


@router.post("/{user_id}/name", response_model=UserOut)
def rename_account(user_id: int, body: NameIn, actor: User = Depends(_any_admin),
                   db: Prisma = Depends(get_db)):
    target = get_account(db, user_id)
    assert_can_manage_account(actor, target)
    return set_name(db, target, body.first_name, body.last_name)


@router.post("/{user_id}/organization", response_model=UserOut)
def move_account_organization(user_id: int, body: MoveOrganizationIn,
                              actor: User = Depends(require_super_admin),
                              db: Prisma = Depends(get_db)):
    target = get_account(db, user_id)
    assert_can_manage_account(actor, target)
    organization = get_organization(db, body.organization_id)
    return move_to_organization(db, target, organization)


@router.delete("/{user_id}", status_code=204)
def delete_account_endpoint(user_id: int, actor: User = Depends(_any_admin),
                            db: Prisma = Depends(get_db)):
    target = get_account(db, user_id)
    assert_can_manage_account(actor, target)
    delete_account(db, target)


@router.get("", response_model=list[UserOut])
def list_accounts(role: Role | None = None, organization_id: int | None = None,
                  actor: User = Depends(_any_admin), db: Prisma = Depends(get_db)):
    filters: list[UserWhereInput] = []
    if role is not None:
        filters.append({"role": role})
    if organization_id is not None:
        filters.append({"organization_id": organization_id})
    where: UserWhereInput | None = {"AND": filters} if filters else None
    return visible_users(db, actor, where, order="username")
