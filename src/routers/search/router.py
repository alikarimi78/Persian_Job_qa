from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool

from src.engine_manager import manager
from src.models import User
from src.rate_limit import search_rate_limit

from .schemas import ProfileSearchIn, ProfileSearchOut, SearchIn, SearchOut

router = APIRouter(tags=["search"])


def ready_engine():
    try:
        return manager.engine
    except RuntimeError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Engine is not ready")


# Encoding and the LLM call are both blocking, so the engine runs off the event loop.
@router.post("/search", response_model=SearchOut)
async def search(body: SearchIn, user: User = Depends(search_rate_limit)):
    engine = ready_engine()
    result = await run_in_threadpool(engine.answer, body.question)
    return result


@router.post("/search/advanced", response_model=ProfileSearchOut)
async def advanced_search(body: ProfileSearchIn, user: User = Depends(search_rate_limit)):
    engine = ready_engine()
    result = await run_in_threadpool(engine.analyze, body.profile)
    return result
