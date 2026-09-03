from fastapi import HTTPException, status
from prisma import Prisma
from prisma.types import UserWhereInput

from .auth import hash_password
from .models import OrganizationSummary, Role, User, scope_organization_id


def get_account(db: Prisma, user_id: int) -> User:
    user = db.user.find_unique(where={"id": user_id})
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")
    return user


def get_organization(db: Prisma, organization_id: int) -> OrganizationSummary:
    org = OrganizationSummary.prisma(db).find_unique(where={"id": organization_id})
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    return org


def organization_of(db: Prisma, user: User) -> OrganizationSummary | None:
    organization_id = scope_organization_id(user)
    if organization_id is None:
        return None
    return OrganizationSummary.prisma(db).find_unique(where={"id": organization_id})


def assert_manages_organization(actor: User, organization_id: int) -> None:
    if actor.role == Role.super_admin:
        return
    if actor.role == Role.org_admin and actor.organization_id == organization_id:
        return
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Outside your organization")


def assert_can_manage_account(actor: User, target: User) -> None:
    if actor.id == target.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You cannot act on your own account")
    if actor.role == Role.super_admin:
        return
    if actor.role == Role.org_admin:
        if target.role == Role.user and target.organization_id == actor.organization_id:
            return
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Outside your organization")
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role")


def delete_account(db: Prisma, target: User) -> None:
    db.user.delete(where={"id": target.id})


def move_to_organization(db: Prisma, target: User, organization: OrganizationSummary) -> User:
    if target.organization_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"A {target.role} does not belong to an organization")
    if target.role == Role.org_admin and organization.id != target.organization_id:
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
    return db.user.update(where={"id": target.id},
                          data={"first_name": first_name, "last_name": last_name})


def create_account(db: Prisma, *, username: str, password: str, role: Role,
                   first_name: str | None = None, last_name: str | None = None,
                   organization_id: int | None = None) -> User:
    if db.user.find_first(where={"username": username}):
        raise HTTPException(status.HTTP_409_CONFLICT, "Username already taken")

    if role == Role.org_admin:
        taken = db.user.find_first(
            where={"role": Role.org_admin, "organization_id": organization_id})
        if taken:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"Organization already has an admin ({taken.username})")

    return db.user.create(data={
        "username": username,
        "hashed_password": hash_password(password),
        "role": role,
        "first_name": first_name,
        "last_name": last_name,
        "organization_id": organization_id,
    })


def visible_scope(actor: User) -> UserWhereInput:
    if actor.role == Role.super_admin:
        return {}
    return {"organization_id": actor.organization_id, "role": Role.user}


def visible_users(db: Prisma, actor: User, where: UserWhereInput | None = None,
                  order: str | None = None) -> list[User]:
    scope = visible_scope(actor)
    clause: UserWhereInput = scope if where is None else {"AND": [scope, where]}
    return db.user.find_many(where=clause,
                             order={"username": "asc"} if order == "username" else None)
