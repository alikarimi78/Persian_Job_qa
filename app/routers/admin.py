from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..auth import require_super_admin
from ..database import get_db
from ..engine_manager import manager
from ..models import User, JobRecord, JobStatus
from ..schemas import JobIn, JobOut, RebuildStatus

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_super_admin)])


@router.get("/suggestions", response_model=list[JobOut])
def list_suggestions(job_status: JobStatus = JobStatus.pending,
                     db: Session = Depends(get_db)):
    return db.query(JobRecord).filter(JobRecord.status == job_status).all()


def _review(job_id: int, new_status: JobStatus, admin: User, db: Session) -> JobRecord:
    record = db.get(JobRecord, job_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Record not found")
    if record.status != JobStatus.pending:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Record is already {record.status.value}")
    record.status = new_status
    record.reviewed_by = admin.id
    db.commit()
    db.refresh(record)
    return record


@router.put("/suggestions/{job_id}", response_model=JobOut)
def edit_suggestion(job_id: int, body: JobIn, db: Session = Depends(get_db)):
    """An admin corrects a pending suggestion before deciding on it.

    The whole record is sent, not a patch: the reviewer edits the same ten-column form
    the suggester filled in, so a partial body would only add a way for a column to go
    missing. `JobIn` is what keeps every column filled either way.

    Editing is deliberately confined to `pending`. An approved record is part of the one
    corpus every organization searches — changing it there is a dataset edit that also
    needs a rebuild before the search reflects it — and a rejected one is closed. Both
    answer 409, the same way a second review does."""
    record = db.get(JobRecord, job_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Record not found")
    if record.status != JobStatus.pending:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Record is already {record.status.value}")
    for column, value in body.model_dump().items():
        setattr(record, column, value)
    db.commit()
    db.refresh(record)
    return record


@router.post("/suggestions/{job_id}/approve", response_model=JobOut)
def approve(job_id: int, admin: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    """Approving also **starts the rebuild**, so the record is searchable without a second
    decision. It is fired after `_review` has committed — the rebuild reads the approved
    rows through a session of its own, and would miss a row still sitting in this one.

    Nothing is awaited: `rebuild_async` swaps the new engine in on a daemon thread while
    the old one keeps serving, and its progress is reported by `/admin/rebuild/status`
    exactly as it is for the button. A `False` here means a rebuild was already running
    and this one is queued behind it, which is not an error to report."""
    record = _review(job_id, JobStatus.approved, admin, db)
    manager.rebuild_async()
    return record


@router.post("/suggestions/{job_id}/reject", response_model=JobOut)
def reject(job_id: int, admin: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    return _review(job_id, JobStatus.rejected, admin, db)


@router.post("/jobs", response_model=JobOut, status_code=201)
def create_job(body: JobIn, admin: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    """A super_admin adds a record directly; it is approved immediately — and rebuilt
    immediately, for the same reason approving a suggestion is: this inserts an approved
    row, and an approved row nobody can find is the same problem either way."""
    record = JobRecord(**body.model_dump(), status=JobStatus.approved,
                       suggested_by=admin.id, reviewed_by=admin.id)
    db.add(record)
    db.commit()
    db.refresh(record)
    manager.rebuild_async()
    return record


@router.post("/rebuild", status_code=202)
def rebuild(force_embeddings: bool = False):
    """Rebuilds the engine from approved records in the background.
    Search keeps serving the old engine until the new one atomically swaps in.

    Only the records whose text the embedding store has never seen are encoded, so
    this is seconds after an approval rather than a full re-encode. `force_embeddings`
    re-encodes the whole corpus and overwrites the store — minutes on GPU, ~31 min on
    CPU — and is only for a store that has gone bad."""
    if not manager.rebuild_async(force_embeddings=force_embeddings):
        raise HTTPException(status.HTTP_409_CONFLICT, "A rebuild is already running")
    return {"detail": "Rebuild started"}


@router.get("/rebuild/status", response_model=RebuildStatus)
def rebuild_status():
    return RebuildStatus(running=manager.rebuilding, last_result=manager.last_result)
