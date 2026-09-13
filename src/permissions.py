from fastapi import HTTPException, status
from prisma.types import UserWhereInput

from .models import Role, User

# The second of the two access checks: `security.require_roles` says *who* may call an
# endpoint, these say *which* records the caller may reach. Both are needed — a role
# alone never authorises a record.


def assert_manages_organization(actor: User, organization_id: int) -> None:
    if actor.role == Role.super_admin:
        return
    if actor.role == Role.org_admin and actor.organization_id == organization_id:
        return
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Outside your organization")


# You may act on the accounts you could have created, and nobody may act on their own
# account — the one exception being `POST /auth/password`, since a super_admin has
# nobody above them.
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


# A job record either sits in the public corpus (organization_id None), which every
# organization searches, or belongs to one organization and is searchable only by its
# accounts. Suggesting is the open end of that: anyone may propose a public record, or
# one for the organization they sit in.
def assert_can_suggest_job(actor: User, organization_id: int | None) -> None:
    if organization_id is None or actor.role == Role.super_admin:
        return
    if organization_id == actor.organization_id:
        return
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Outside your organization")


# Admitting one is the narrow end: `/admin` writes what a search can reach, so an
# org_admin reaches exactly their own organization's records and never the public
# corpus — theirs to run, everyone's to read.
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


# The organizations whose records this account may search: None is "every one of them",
# and the set's own `None` is the public corpus. `job_qa_service` maps the set onto its
# records; nothing here knows how it stores them.
def visible_job_organizations(actor: User) -> set[int | None] | None:
    if actor.role == Role.super_admin:
        return None
    return {None, actor.organization_id}
