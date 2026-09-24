from fastapi import APIRouter, Depends
from prisma import Prisma

from src.database import get_db
from src.models import JobStatus, User
from src.permissions import assert_can_suggest_job
from src.security import get_current_user
from src.routers.orgs.service import get_organization

from .schemas import JobIn, JobOut

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/suggestions", response_model=JobOut, status_code=201)
def suggest_job(body: JobIn, user: User = Depends(get_current_user),
                db: Prisma = Depends(get_db)):
    assert_can_suggest_job(user, body.organization_id)
    if body.organization_id is not None:
        get_organization(db, body.organization_id)
    return db.jobrecord.create(data={**body.model_dump(), "status": JobStatus.pending,
                                     "suggested_by": user.id})


@router.get("/suggestions/mine", response_model=list[JobOut])
def my_suggestions(user: User = Depends(get_current_user), db: Prisma = Depends(get_db)):
    return db.jobrecord.find_many(where={"suggested_by": user.id})
