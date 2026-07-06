from fastapi import APIRouter, HTTPException, status
from fastapi.concurrency import run_in_threadpool

from ..engine_manager import manager
from ..schemas import SearchIn, SearchOut

router = APIRouter(tags=["search"])


@router.post("/search", response_model=SearchOut)
async def search(body: SearchIn):
    """Public endpoint: anonymous and logged-in users both search here."""
    try:
        engine = manager.engine
    except RuntimeError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Engine is not ready")
    # Encoding + LLM call are blocking -> keep the event loop free
    result = await run_in_threadpool(engine.answer, body.question)
    return result
