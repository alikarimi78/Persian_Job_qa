"""Aggregate counts for the dashboard.

Scoped by exactly the rules `/accounts` uses — `accounts.visible_users` for the roster,
and the same organization/unit narrowing the units router applies — so the dashboard can
never total up something its caller is not allowed to list. An ordinary user has no
dashboard at all: the role gate is the three admin roles, as on `/accounts`.

Everything is a count. That is deliberate: an org_admin already sees the accounts behind
these numbers, and a number nobody is entitled to would be a leak no aggregation hides.
"""

from collections import Counter
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..accounts import visible_users
from ..auth import require_roles
from ..database import get_db
from ..engine_manager import manager
from ..models import JobRecord, JobStatus, Organization, Role, Unit, User
from ..schemas import JobStats, RoleCount, SeriesPoint, StatsOut, UnitCount

router = APIRouter(prefix="/stats", tags=["stats"])


def _series(values) -> list[SeriesPoint]:
    """Creation timestamps as one count per calendar day, oldest first.

    Grouped in Python rather than in SQL: `date_trunc` is Postgres-only, a CAST to DATE
    means something else again in the SQLite the tests run on, and this is one column of
    a few thousand rows. `created_at` is NOT NULL in the schema, but a row written
    around a migration can still arrive here without one — it is skipped rather than
    counted against an invented day.
    """
    days = Counter(v.date().isoformat() for v in values if isinstance(v, datetime))
    return [SeriesPoint(date=day, count=count) for day, count in sorted(days.items())]


def _visible_units(db: Session, actor: User):
    """The units the caller may see — the same narrowing `GET /units` applies."""
    query = db.query(Unit)
    if actor.role is Role.org_admin:
        return query.filter(Unit.organization_id == actor.organization_id)
    if actor.role is Role.unit_admin:
        return query.filter(Unit.id == actor.unit_id)
    return query


def _visible_organizations(db: Session, actor: User) -> list[Organization]:
    if actor.role is Role.super_admin:
        return db.query(Organization).all()
    organization_id = actor.scope_organization_id
    if organization_id is None:
        return []
    org = db.get(Organization, organization_id)
    return [org] if org is not None else []


@router.get("", response_model=StatsOut)
def stats(actor: User = Depends(require_roles(Role.super_admin, Role.org_admin,
                                              Role.unit_admin)),
          db: Session = Depends(get_db)):
    accounts = visible_users(db, actor).all()
    units = _visible_units(db, actor).all()
    organizations = _visible_organizations(db, actor)

    per_unit = Counter(a.unit_id for a in accounts if a.unit_id is not None)
    per_role = Counter(a.role.value for a in accounts)

    # `visible_users` answers "whose accounts may this admin manage", which leaves the
    # caller's own row out — an admin does not manage themselves. Their own suggestions
    # are still their organization's, so the job scope adds them back.
    scope_ids = {a.id for a in accounts} | {actor.id}

    jobs = db.query(JobRecord)
    if actor.role is not Role.super_admin:
        jobs = jobs.filter(JobRecord.suggested_by.in_(scope_ids))
    job_rows = jobs.all()
    by_status = Counter(r.status for r in job_rows)

    scope = ("global" if actor.role is Role.super_admin
             else "organization" if actor.role is Role.org_admin else "unit")
    scope_name = None
    if actor.role is Role.org_admin and organizations:
        scope_name = organizations[0].name
    elif actor.role is Role.unit_admin and units:
        scope_name = units[0].name

    return StatsOut(
        scope=scope,
        scope_name=scope_name,
        organizations=len(organizations),
        units=len(units),
        accounts=len(accounts),
        accounts_active=sum(1 for a in accounts if a.is_active),
        accounts_blocked=sum(1 for a in accounts if not a.is_active),
        # Every role the hierarchy has, including the ones sitting at zero: a bar that
        # disappears when a unit empties reads as a missing category rather than a count.
        accounts_by_role=[RoleCount(role=role.value, count=per_role.get(role.value, 0))
                          for role in Role],
        accounts_per_unit=[UnitCount(unit_id=u.id, name=u.name, accounts=per_unit.get(u.id, 0))
                           for u in units],
        jobs=JobStats(
            corpus_records=db.query(JobRecord)
                             .filter(JobRecord.status == JobStatus.approved).count(),
            engine_records=manager.record_count,
            pending=by_status.get(JobStatus.pending, 0),
            approved=by_status.get(JobStatus.approved, 0),
            rejected=by_status.get(JobStatus.rejected, 0),
        ),
        accounts_series=_series(a.created_at for a in accounts),
        units_series=_series(u.created_at for u in units),
        organizations_series=_series(o.created_at for o in organizations),
        suggestions_series=_series(r.created_at for r in job_rows),
    )
