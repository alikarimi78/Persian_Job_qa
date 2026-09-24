from fastapi import APIRouter, Depends, HTTPException, status
from prisma import Prisma

from src.database import get_db
from src.engine_manager import manager
from src.models import JobRecord, JobStatus, Role, User
from src.permissions import assert_can_admit_job, visible_job_organizations
from src.security import require_roles, require_super_admin
from src.routers.jobs.schemas import JobIn, JobOut, JobPage
from src.routers.orgs.service import get_organization

from .schemas import RebuildStatus
from .service import (JOBS_PAGE_MAX, JOBS_PAGE_SIZE, approved, matching_ids,
                      organization_filter, pending, review, target_owner, update_data)

any_admin = require_roles(Role.super_admin, Role.org_admin)

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(any_admin)])


def _owner(db: Prisma, actor: User, body: JobIn, record: JobRecord | None = None) -> int | None:
    organization_id = (body.organization_id if record is None
                       else target_owner(body, record))
    assert_can_admit_job(actor, organization_id)
    if organization_id is not None:
        get_organization(db, organization_id)
    return organization_id


@router.get("/suggestions", response_model=list[JobOut])
def list_suggestions(job_status: JobStatus = JobStatus.pending,
                     organization_id: int | None = None, public: bool = False,
                     actor: User = Depends(any_admin), db: Prisma = Depends(get_db)):
    return db.jobrecord.find_many(
        where={"status": job_status,
               **organization_filter(actor, organization_id, public)})


@router.put("/suggestions/{job_id}", response_model=JobOut)
def edit_suggestion(job_id: int, body: JobIn, actor: User = Depends(any_admin),
                    db: Prisma = Depends(get_db)):
    record = pending(db, job_id, actor)
    owner = _owner(db, actor, body, record)
    return db.jobrecord.update(where={"id": job_id}, data=update_data(body, owner))


@router.post("/suggestions/{job_id}/approve", response_model=JobOut)
def approve(job_id: int, admin: User = Depends(any_admin), db: Prisma = Depends(get_db)):
    record = review(db, job_id, JobStatus.approved, admin)
    manager.rebuild_async()
    return record


@router.post("/suggestions/{job_id}/reject", response_model=JobOut)
def reject(job_id: int, admin: User = Depends(any_admin), db: Prisma = Depends(get_db)):
    return review(db, job_id, JobStatus.rejected, admin)


@router.post("/jobs", response_model=JobOut, status_code=201)
def create_job(body: JobIn, admin: User = Depends(any_admin), db: Prisma = Depends(get_db)):
    _owner(db, admin, body)
    record = db.jobrecord.create(data={**body.model_dump(), "status": JobStatus.approved,
                                       "suggested_by": admin.id, "reviewed_by": admin.id})
    manager.rebuild_async()
    return record


@router.get("/jobs", response_model=JobPage)
def list_jobs(q: str = "", page: int = 1, page_size: int = JOBS_PAGE_SIZE,
              job_status: JobStatus = JobStatus.approved,
              organization_id: int | None = None, public: bool = False,
              actor: User = Depends(any_admin), db: Prisma = Depends(get_db)):
    page = max(page, 1)
    page_size = min(max(page_size, 1), JOBS_PAGE_MAX)
    reach = visible_job_organizations(actor)
    where: dict = {"status": job_status,
                   **organization_filter(actor, organization_id, public, reach)}

    if q.strip():
        ids = matching_ids(db, where, q)
        total = len(ids)
        wanted = ids[(page - 1) * page_size:page * page_size]
        items = (db.jobrecord.find_many(where={"id": {"in": wanted}},
                                        order={"job_title": "asc"}) if wanted else [])
    else:
        total = db.jobrecord.count(where=where)
        items = db.jobrecord.find_many(where=where, skip=(page - 1) * page_size,
                                       take=page_size, order={"job_title": "asc"})
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.put("/jobs/{job_id}", response_model=JobOut)
def edit_job(job_id: int, body: JobIn, actor: User = Depends(any_admin),
             db: Prisma = Depends(get_db)):
    current = approved(db, job_id, actor)
    owner = _owner(db, actor, body, current)
    record = db.jobrecord.update(where={"id": job_id}, data=update_data(body, owner))
    manager.rebuild_async()
    return record


@router.delete("/jobs/{job_id}", status_code=204)
def delete_job(job_id: int, actor: User = Depends(any_admin), db: Prisma = Depends(get_db)):
    approved(db, job_id, actor)
    db.jobrecord.delete(where={"id": job_id})
    manager.rebuild_async()


@router.post("/rebuild", status_code=202, dependencies=[Depends(require_super_admin)])
def rebuild(force_embeddings: bool = False):
    if not manager.rebuild_async(force_embeddings=force_embeddings):
        raise HTTPException(status.HTTP_409_CONFLICT, "A rebuild is already running")
    return {"detail": "Rebuild started"}


@router.get("/rebuild/status", response_model=RebuildStatus)
def rebuild_status():
    return RebuildStatus(running=manager.rebuilding, last_result=manager.last_result)
