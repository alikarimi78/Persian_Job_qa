from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool

from ..engine_manager import manager
from ..models import User
from ..rate_limit import search_rate_limit
from ..schemas import SearchIn, SearchOut

router = APIRouter(tags=["search"])


@router.post("/search", response_model=SearchOut)
async def search(body: SearchIn, user: User = Depends(search_rate_limit)):
    """Any provisioned account may search: users, and admins of every level.

    Authentication is required — the system is only for people an admin put in it, so
    there is no anonymous entry point. Nothing about the answer depends on who asks:
    the corpus is global, shared by every organization.

    `search_rate_limit` is `get_current_user` plus a per-account ceiling: this is the
    expensive endpoint (one encode and one LLM call per question), so the budget follows
    the account rather than an IP a whole unit shares.
    """
    try:
        engine = manager.engine
    except RuntimeError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Engine is not ready")
    # Encoding + LLM call are blocking -> keep the event loop free
    result = await run_in_threadpool(engine.answer, body.question)
    return result
