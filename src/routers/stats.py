"""Aggregate counts for the dashboard.

Scoped by exactly the rules `/accounts` uses — `accounts.visible_users` for the roster —
so the dashboard can never total up something its caller is not allowed to list. An
ordinary user has no dashboard at all: the role gate is the two admin roles, as on
`/accounts`.

Everything is a count. That is deliberate: an org_admin already sees the accounts behind
these numbers, and a number nobody is entitled to would be a leak no aggregation hides.
"""

from collections import Counter
from datetime import datetime

from fastapi import APIRouter, Depends
from prisma import Prisma

from ..accounts import visible_users
from ..auth import require_roles
from ..database import get_db
from ..engine_manager import manager
from ..models import JobStatus, OrganizationSummary, Role, User, scope_organization_id
from ..schemas import JobStats, RoleCount, SeriesPoint, StatsOut

router = APIRouter(prefix="/stats", tags=["stats"])


def _series(values) -> list[SeriesPoint]:
    """Creation timestamps as one count per calendar day, oldest first.

    Grouped in Python rather than in SQL: `date_trunc` is Postgres-only, Prisma has no
    portable date-truncating aggregate of its own, and this is one column of a few
    thousand rows. `created_at` is NOT NULL in the schema, but a row written around a
    migration can still arrive here without one — it is skipped rather than counted
    against an invented day.
    """
    days = Counter(v.date().isoformat() for v in values if isinstance(v, datetime))
    return [SeriesPoint(date=day, count=count) for day, count in sorted(days.items())]


def _visible_organizations(db: Prisma, actor: User) -> list[OrganizationSummary]:
    """Narrowed, like every other organization read outside `GET /orgs/{id}/logo`: this
    one only counts them and names one, and the full model would carry an image per row
    onto the dashboard."""
    if actor.role == Role.super_admin:
        return OrganizationSummary.prisma(db).find_many()
    organization_id = scope_organization_id(actor)
    if organization_id is None:
        return []
    org = OrganizationSummary.prisma(db).find_unique(where={"id": organization_id})
    return [org] if org is not None else []


@router.get("", response_model=StatsOut)
def stats(actor: User = Depends(require_roles(Role.super_admin, Role.org_admin)),
          db: Prisma = Depends(get_db)):
    accounts = visible_users(db, actor)
    organizations = _visible_organizations(db, actor)

    per_role = Counter(a.role for a in accounts)

    # `visible_users` answers "whose accounts may this admin manage", which leaves the
    # caller's own row out — an admin does not manage themselves. Their own suggestions
    # are still their organization's, so the job scope adds them back.
    scope_ids = {a.id for a in accounts} | {actor.id}

    job_rows = db.jobrecord.find_many(
        where={} if actor.role == Role.super_admin
        else {"suggested_by": {"in": sorted(scope_ids)}})
    by_status = Counter(r.status for r in job_rows)

    scope = "global" if actor.role == Role.super_admin else "organization"
    scope_name = (organizations[0].name
                  if actor.role == Role.org_admin and organizations else None)

    return StatsOut(
        scope=scope,
        scope_name=scope_name,
        organizations=len(organizations),
        accounts=len(accounts),
        accounts_active=sum(1 for a in accounts if a.is_active),
        accounts_blocked=sum(1 for a in accounts if not a.is_active),
        # Every role the hierarchy has, including the ones sitting at zero: a bar that
        # disappears when an organization empties reads as a missing category rather
        # than a count.
        accounts_by_role=[RoleCount(role=role, count=per_role.get(role, 0))
                          for role in Role],
        jobs=JobStats(
            corpus_records=db.jobrecord.count(where={"status": JobStatus.approved}),
            engine_records=manager.record_count,
            pending=by_status.get(JobStatus.pending, 0),
            approved=by_status.get(JobStatus.approved, 0),
            rejected=by_status.get(JobStatus.rejected, 0),
        ),
        accounts_series=_series(a.created_at for a in accounts),
        organizations_series=_series(o.created_at for o in organizations),
        suggestions_series=_series(r.created_at for r in job_rows),
    )
