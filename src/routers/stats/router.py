from collections import Counter
from datetime import datetime

from fastapi import APIRouter, Depends
from prisma import Prisma
from prisma.types import JobRecordWhereInput

from src.database import get_db
from src.engine_manager import manager
from src.models import (JobStatus, OrganizationSummary, Role, User,
                        scope_organization_id)
from src.permissions import assert_manages_organization
from src.security import require_roles
from src.routers.accounts.service import visible_users
from src.routers.orgs.service import job_counts

from .schemas import JobStats, OrganizationCount, RoleCount, SeriesPoint, StatsOut

router = APIRouter(prefix="/stats", tags=["stats"])


def _series(values) -> list[SeriesPoint]:
    days = Counter(v.date().isoformat() for v in values if isinstance(v, datetime))
    return [SeriesPoint(date=day, count=count) for day, count in sorted(days.items())]


def _visible_organizations(db: Prisma, actor: User,
                           organization_id: int | None) -> list[OrganizationSummary]:
    if organization_id is None and actor.role != Role.super_admin:
        organization_id = scope_organization_id(actor)
    if organization_id is None:
        return OrganizationSummary.prisma(db).find_many()
    org = OrganizationSummary.prisma(db).find_unique(where={"id": organization_id})
    return [org] if org is not None else []


# Counts only, scoped the way `/accounts` is: the account totals come from
# `visible_users`, so they can never exceed what this caller could have listed. The
# `corpus_records`/`engine_records` gap is what a rebuild would pick up. `organization_id`
# narrows every number to one organization — the dashboard's filter — and an org_admin
# is narrowed to their own whether they ask or not.
@router.get("", response_model=StatsOut)
def stats(organization_id: int | None = None,
          actor: User = Depends(require_roles(Role.super_admin, Role.org_admin)),
          db: Prisma = Depends(get_db)):
    if organization_id is not None:
        assert_manages_organization(actor, organization_id)
    filtered = organization_id is not None or actor.role != Role.super_admin

    accounts = visible_users(db, actor,
                             {"organization_id": organization_id} if organization_id else None)
    organizations = _visible_organizations(db, actor, organization_id)

    per_role = Counter(a.role for a in accounts)

    # An organization's job activity is what its people proposed plus what belongs to
    # it however it got there — a record the super admin added for them is theirs.
    scope_ids = {a.id for a in accounts}
    if actor.role != Role.super_admin:
        scope_ids.add(actor.id)
    scope_organizations = [org.id for org in organizations]
    where: JobRecordWhereInput | None = None
    if filtered:
        where = {"OR": [{"suggested_by": {"in": sorted(scope_ids)}},
                        {"organization_id": {"in": scope_organizations}}]}
    job_rows = db.jobrecord.find_many(where=where) if where else db.jobrecord.find_many()
    by_status = Counter(r.status for r in job_rows)

    owned = job_counts(db, scope_organizations, status=JobStatus.approved)

    scope = "organization" if filtered else "global"
    scope_name = organizations[0].name if filtered and organizations else None

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
            public_records=db.jobrecord.count(
                where={"status": JobStatus.approved, "organization_id": None}),
            organization_records=sum(owned.values()),
        ),
        jobs_by_organization=[
            OrganizationCount(organization_id=org.id, name=org.name,
                              count=owned.get(org.id, 0))
            for org in organizations],
        accounts_series=_series(a.created_at for a in accounts),
        organizations_series=_series(o.created_at for o in organizations),
        suggestions_series=_series(r.created_at for r in job_rows),
    )
