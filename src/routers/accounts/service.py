from fastapi import HTTPException, status
from prisma import Prisma
from prisma.types import UserInclude, UserWhereInput

from src.models import OrganizationSummary, Role, User, scope_organization_id
from src.permissions import visible_scope
from src.security import hash_password


def get_account(db: Prisma, user_id: int) -> User:
    user = db.user.find_unique(where={"id": user_id})
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")
    return user


def organization_of(db: Prisma, user: User) -> OrganizationSummary | None:
    organization_id = scope_organization_id(user)
    if organization_id is None:
        return None
    return OrganizationSummary.prisma(db).find_unique(where={"id": organization_id})


def delete_account(db: Prisma, target: User) -> None:
    db.user.delete(where={"id": target.id})


# Moving an org_admin needs an admin-free destination — `uq_users_org_admin` would
# refuse it anyway, but a 409 naming the sitting admin is the useful answer.
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
                   organization_id: int | None = None, created_by: int | None = None) -> User:
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
        "created_by": created_by,
    })


# Raw, because a login is not an edit: any client update moves `@updatedAt`, and passing the stored value
# back would undo an admin's edit made during the password check.
def record_login(db: Prisma, user: User) -> None:
    db.execute_raw("""UPDATE "users" SET "last_login" = NOW() AT TIME ZONE 'UTC' WHERE "id" = $1""",
                   user.id)


# `GET /stats` counts what this returns, so it can never total what its caller could
# not have listed.
def visible_users(db: Prisma, actor: User, where: UserWhereInput | None = None,
                  order: str | None = None, include: UserInclude | None = None) -> list[User]:
    scope = visible_scope(actor)
    clause: UserWhereInput = scope if where is None else {"AND": [scope, where]}
    return db.user.find_many(where=clause, include=include,
                             order={"username": "asc"} if order == "username" else None)
