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
