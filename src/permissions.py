from fastapi import HTTPException, status
from prisma.types import UserWhereInput

from .models import Role, User


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


def visible_scope(actor: User) -> UserWhereInput:
    if actor.role == Role.super_admin:
        return {}
    return {"organization_id": actor.organization_id, "role": Role.user}


def assert_can_suggest_job(actor: User, organization_id: int | None) -> None:
    if organization_id is None or actor.role == Role.super_admin:
        return
    if organization_id == actor.organization_id:
        return
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Outside your organization")


def assert_can_admit_job(actor: User, organization_id: int | None) -> None:
    if actor.role == Role.super_admin:
        return
    if actor.role == Role.org_admin and organization_id is not None \
            and organization_id == actor.organization_id:
        return
    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        "Public records are admitted by the system administrator"
        if organization_id is None else "Outside your organization")


def visible_job_organizations(actor: User) -> set[int | None] | None:
    if actor.role == Role.super_admin:
        return None
    return {None, actor.organization_id}
