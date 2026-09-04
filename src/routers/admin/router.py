from fastapi import APIRouter, Depends, HTTPException, status
from prisma import Prisma

from src.database import get_db
from src.engine_manager import manager
from src.models import JobStatus, User
from src.security import require_super_admin
from src.routers.jobs.schemas import JobIn, JobOut, JobPage

from .schemas import RebuildStatus
from .service import (JOBS_PAGE_MAX, JOBS_PAGE_SIZE, approved, pending, review,
                      title_filters)

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_super_admin)])


@router.get("/suggestions", response_model=list[JobOut])
def list_suggestions(job_status: JobStatus = JobStatus.pending,
                     db: Prisma = Depends(get_db)):
    return db.jobrecord.find_many(where={"status": job_status})


# The third review action: the reviewer corrects the record before deciding, rather
# than rejecting it over one column.
@router.put("/suggestions/{job_id}", response_model=JobOut)
def edit_suggestion(job_id: int, body: JobIn, db: Prisma = Depends(get_db)):
    pending(db, job_id)
    return db.jobrecord.update(where={"id": job_id}, data=body.model_dump())


# The rebuild is started after `review` has written the row: Prisma commits each write
# on its own and the rebuild re-queries, so that ordering is what makes the record
# visible to the next search.
@router.post("/suggestions/{job_id}/approve", response_model=JobOut)
def approve(job_id: int, admin: User = Depends(require_super_admin), db: Prisma = Depends(get_db)):
    record = review(db, job_id, JobStatus.approved, admin)
    manager.rebuild_async()
    return record


@router.post("/suggestions/{job_id}/reject", response_model=JobOut)
def reject(job_id: int, admin: User = Depends(require_super_admin), db: Prisma = Depends(get_db)):
    return review(db, job_id, JobStatus.rejected, admin)


@router.post("/jobs", response_model=JobOut, status_code=201)
def create_job(body: JobIn, admin: User = Depends(require_super_admin), db: Prisma = Depends(get_db)):
    record = db.jobrecord.create(data={**body.model_dump(), "status": JobStatus.approved,
                                       "suggested_by": admin.id, "reviewed_by": admin.id})
    manager.rebuild_async()
    return record


# `page`/`page_size` are clamped rather than validated — a 422 here is enough to take
# the admin panel down.
@router.get("/jobs", response_model=JobPage)
def list_jobs(q: str = "", page: int = 1, page_size: int = JOBS_PAGE_SIZE,
              job_status: JobStatus = JobStatus.approved, db: Prisma = Depends(get_db)):
    page = max(page, 1)
    page_size = min(max(page_size, 1), JOBS_PAGE_MAX)
    where: dict = {"status": job_status}
    if q.strip():
        where["OR"] = title_filters(q)

    total = db.jobrecord.count(where=where)
    items = db.jobrecord.find_many(where=where, skip=(page - 1) * page_size,
                                   take=page_size, order={"job_title": "asc"})
    return {"items": items, "total": total, "page": page, "page_size": page_size}


# `reviewed_by` is left alone: it records who admitted the record, and an edit is not a
# second admission.
@router.put("/jobs/{job_id}", response_model=JobOut)
def edit_job(job_id: int, body: JobIn, db: Prisma = Depends(get_db)):
    approved(db, job_id)
    record = db.jobrecord.update(where={"id": job_id}, data=body.model_dump())
    manager.rebuild_async()
    return record


@router.post("/rebuild", status_code=202)
def rebuild(force_embeddings: bool = False):
    if not manager.rebuild_async(force_embeddings=force_embeddings):
        raise HTTPException(status.HTTP_409_CONFLICT, "A rebuild is already running")
    return {"detail": "Rebuild started"}


@router.get("/rebuild/status", response_model=RebuildStatus)
def rebuild_status():
    return RebuildStatus(running=manager.rebuilding, last_result=manager.last_result)
