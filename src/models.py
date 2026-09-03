from prisma.enums import JobStatus, Role
from prisma.models import JobRecord, Organization, User
from prisma.partials import OrganizationSummary

__all__ = ["JobRecord", "JobStatus", "Organization", "OrganizationSummary", "Role",
           "User", "display_name", "full_name", "has_logo", "join_name",
           "scope_organization_id"]


def join_name(first_name: str | None, last_name: str | None) -> str | None:
    parts = [part for part in (first_name, last_name) if part]
    return " ".join(parts) if parts else None


def full_name(user: User) -> str | None:
    return join_name(user.first_name, user.last_name)


def display_name(user: User) -> str:
    return full_name(user) or user.username


def has_logo(org: Organization | OrganizationSummary) -> bool:
    return org.logo_mime is not None


def scope_organization_id(user: User) -> int | None:
    return user.organization_id
