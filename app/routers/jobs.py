from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import get_db
from ..models import User, JobRecord, JobStatus
from ..schemas import JobIn, JobOut

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/suggestions", response_model=JobOut, status_code=201)
def suggest_job(body: JobIn, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    """Logged-in users submit a full record; it waits for admin review."""
    record = JobRecord(**body.model_dump(), status=JobStatus.pending, suggested_by=user.id)
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@router.get("/suggestions/mine", response_model=list[JobOut])
def my_suggestions(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(JobRecord).filter(JobRecord.suggested_by == user.id).all()
