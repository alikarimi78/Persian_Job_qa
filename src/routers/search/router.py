from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool

from src.engine_manager import manager
from src.models import User
from src.permissions import visible_job_organizations
from src.rate_limit import search_rate_limit
from src.security import get_current_user

from .schemas import ProfileSearchIn, ProfileSearchOut, SearchIn, SearchOut, VocabularyOut

router = APIRouter(tags=["search"])


def ready_engine():
    try:
        return manager.engine
    except RuntimeError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Engine is not ready")


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


@router.get("/search/vocabulary", response_model=VocabularyOut)
async def vocabulary(user: User = Depends(get_current_user)):
    engine = ready_engine()
    fields = await run_in_threadpool(engine.vocabulary, scope=visible_job_organizations(user))
    return {"fields": fields}
