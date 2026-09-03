from fastapi import APIRouter, Depends, HTTPException, status
from prisma import Prisma

from ..auth import require_super_admin
from ..database import get_db
from ..engine_manager import manager
from ..models import User, JobRecord, JobStatus
from ..schemas import JobIn, JobOut, JobPage, RebuildStatus

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_super_admin)])


@router.get("/suggestions", response_model=list[JobOut])
def list_suggestions(job_status: JobStatus = JobStatus.pending,
                     db: Prisma = Depends(get_db)):
    return db.jobrecord.find_many(where={"status": job_status})


def _pending(db: Prisma, job_id: int) -> JobRecord:
    record = db.jobrecord.find_unique(where={"id": job_id})
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Record not found")
    if record.status != JobStatus.pending:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Record is already {record.status}")
    return record


def _review(job_id: int, new_status: JobStatus, admin: User, db: Prisma) -> JobRecord:
    _pending(db, job_id)
    return db.jobrecord.update(
        where={"id": job_id},
        data={"status": new_status, "reviewer": {"connect": {"id": admin.id}}})


@router.put("/suggestions/{job_id}", response_model=JobOut)
def edit_suggestion(job_id: int, body: JobIn, db: Prisma = Depends(get_db)):
    _pending(db, job_id)
    return db.jobrecord.update(where={"id": job_id}, data=body.model_dump())


@router.post("/suggestions/{job_id}/approve", response_model=JobOut)
def approve(job_id: int, admin: User = Depends(require_super_admin), db: Prisma = Depends(get_db)):
    record = _review(job_id, JobStatus.approved, admin, db)
    manager.rebuild_async()
    return record


@router.post("/suggestions/{job_id}/reject", response_model=JobOut)
def reject(job_id: int, admin: User = Depends(require_super_admin), db: Prisma = Depends(get_db)):
    return _review(job_id, JobStatus.rejected, admin, db)


@router.post("/jobs", response_model=JobOut, status_code=201)
def create_job(body: JobIn, admin: User = Depends(require_super_admin), db: Prisma = Depends(get_db)):
    record = db.jobrecord.create(data={**body.model_dump(), "status": JobStatus.approved,
                                       "suggested_by": admin.id, "reviewed_by": admin.id})
    manager.rebuild_async()
    return record


JOBS_PAGE_SIZE = 20
JOBS_PAGE_MAX = 100

_ZWNJ = "\u200c"


def _title_filters(query: str) -> list[dict]:
    query = query.strip().replace("ي", "ی").replace("ك", "ک")
    forms = {query, query.replace(" ", _ZWNJ), query.replace(_ZWNJ, " ")}
    return [{"job_title": {"contains": form, "mode": "insensitive"}}
            for form in forms if form]


@router.get("/jobs", response_model=JobPage)
def list_jobs(q: str = "", page: int = 1, page_size: int = JOBS_PAGE_SIZE,
              job_status: JobStatus = JobStatus.approved, db: Prisma = Depends(get_db)):
    page = max(page, 1)
    page_size = min(max(page_size, 1), JOBS_PAGE_MAX)
    where: dict = {"status": job_status}
    if q.strip():
        where["OR"] = _title_filters(q)

    total = db.jobrecord.count(where=where)
    items = db.jobrecord.find_many(where=where, skip=(page - 1) * page_size,
                                   take=page_size, order={"job_title": "asc"})
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def _approved(db: Prisma, job_id: int) -> JobRecord:
    record = db.jobrecord.find_unique(where={"id": job_id})
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Record not found")
    if record.status != JobStatus.approved:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Record is {record.status}; a suggestion is edited through /admin/suggestions")
    return record


@router.put("/jobs/{job_id}", response_model=JobOut)
def edit_job(job_id: int, body: JobIn, db: Prisma = Depends(get_db)):
    _approved(db, job_id)
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
