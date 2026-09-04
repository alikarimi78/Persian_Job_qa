from collections import Counter
from datetime import datetime

from fastapi import APIRouter, Depends
from prisma import Prisma

from src.database import get_db
from src.engine_manager import manager
from src.models import (JobStatus, OrganizationSummary, Role, User,
                        scope_organization_id)
from src.security import require_roles
from src.routers.accounts.service import visible_users

from .schemas import JobStats, RoleCount, SeriesPoint, StatsOut

router = APIRouter(prefix="/stats", tags=["stats"])


def _series(values) -> list[SeriesPoint]:
    days = Counter(v.date().isoformat() for v in values if isinstance(v, datetime))
    return [SeriesPoint(date=day, count=count) for day, count in sorted(days.items())]


def _visible_organizations(db: Prisma, actor: User) -> list[OrganizationSummary]:
    if actor.role == Role.super_admin:
        return OrganizationSummary.prisma(db).find_many()
    organization_id = scope_organization_id(actor)
    if organization_id is None:
        return []
    org = OrganizationSummary.prisma(db).find_unique(where={"id": organization_id})
    return [org] if org is not None else []


# Counts only, scoped the way `/accounts` is: the account totals come from
# `visible_users`, so they can never exceed what this caller could have listed. The
# `corpus_records`/`engine_records` gap is what a rebuild would pick up.
@router.get("", response_model=StatsOut)
def stats(actor: User = Depends(require_roles(Role.super_admin, Role.org_admin)),
          db: Prisma = Depends(get_db)):
    accounts = visible_users(db, actor)
    organizations = _visible_organizations(db, actor)

    per_role = Counter(a.role for a in accounts)

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
