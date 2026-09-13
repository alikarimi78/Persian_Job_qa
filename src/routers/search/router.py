from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool

from src.engine_manager import manager
from src.models import User
from src.permissions import visible_job_organizations
from src.rate_limit import search_rate_limit

from .schemas import ProfileSearchIn, ProfileSearchOut, SearchIn, SearchOut

router = APIRouter(tags=["search"])


def ready_engine():
    try:
        return manager.engine
    except RuntimeError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Engine is not ready")


# Both paths run against the caller's own reach: the public corpus plus their own
# organization's records, and every record for a super_admin. A record another
# organization owns is not refused — it is not there at all, so a question about it is
# answered the way a question about anything the corpus lacks is, by composing one.
@router.post("/search", response_model=SearchOut)
async def search(body: SearchIn, user: User = Depends(search_rate_limit)):
    engine = ready_engine()
    result = await run_in_threadpool(engine.answer, body.question,
                                     scope=visible_job_organizations(user))
    return result


@router.post("/search/advanced", response_model=ProfileSearchOut)
async def advanced_search(body: ProfileSearchIn, user: User = Depends(search_rate_limit)):
    engine = ready_engine()
    result = await run_in_threadpool(engine.analyze, body.profile,
                                     scope=visible_job_organizations(user))
    return result
