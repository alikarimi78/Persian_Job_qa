from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool

from ..engine_manager import manager
from ..models import User
from ..rate_limit import search_rate_limit
from ..schemas import ProfileSearchIn, ProfileSearchOut, SearchIn, SearchOut

router = APIRouter(tags=["search"])


def ready_engine():
    try:
        return manager.engine
    except RuntimeError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Engine is not ready")


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
    engine = ready_engine()
    # Encoding + LLM call are blocking -> keep the event loop free
    result = await run_in_threadpool(engine.answer, body.question)
    return result


@router.post("/search/advanced", response_model=ProfileSearchOut)
async def advanced_search(body: ProfileSearchIn, user: User = Depends(search_rate_limit)):
    """Ranks the corpus against a described profile instead of answering a question.

    The other half of `/search`, not a variant of it. There the user has a job in mind;
    here they have a list of what they can do and want to know which records it adds up
    to, so the answer is a ranking with a per-item breakdown rather than one paragraph
    about one job. It never designs a new record — that stays with the free-text path,
    which is where someone who is describing a job they want already is.

    Costs the same as `/search` (one encode, one LLM call) and is on the same per-account
    budget for that reason. The coverage half costs nothing: it is set arithmetic over
    tokens the engine split once at build time.
    """
    engine = ready_engine()
    result = await run_in_threadpool(engine.analyze, body.profile)
    return result
