from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool

from ..auth import get_current_user
from ..engine_manager import manager
from ..models import User
from ..schemas import SearchIn, SearchOut

router = APIRouter(tags=["search"])


@router.post("/search", response_model=SearchOut)
async def search(body: SearchIn, user: User = Depends(get_current_user)):
    """Any provisioned account may search: users, and admins of every level.

    Authentication is required — the system is only for people an admin put in it, so
    there is no anonymous entry point. Nothing about the answer depends on who asks:
    the corpus is global, shared by every organization.
    """
    try:
        engine = manager.engine
    except RuntimeError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Engine is not ready")
    # Encoding + LLM call are blocking -> keep the event loop free
    result = await run_in_threadpool(engine.answer, body.question)
    return result
