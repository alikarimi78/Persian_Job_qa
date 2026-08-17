from fastapi import APIRouter, Depends, Response
from fastapi.concurrency import run_in_threadpool
from prisma import Prisma

from ..accounts import organization_of
from ..auth import get_current_user
from ..database import get_db
from ..models import User
from ..reports import filename, render_pdf
from ..schemas import ReportIn

router = APIRouter(prefix="/reports", tags=["reports"])


@router.post(
    "/search",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}},
                     "description": "The report, as a PDF file"}},
)
async def search_report(body: ReportIn, user: User = Depends(get_current_user),
                        db: Prisma = Depends(get_db)):
    """Prints an answer the caller already has.

    The client posts back the `SearchOut` on its screen instead of naming a question to
    re-run: an answer costs an LLM call, the model is not deterministic, and a report
    that quietly differed from the page it was printed from would be worse than no
    report. Nothing is stored — the PDF is built and handed over.

    No rate limit of its own. The expensive endpoint is `/search`, which is already
    capped per account; this one neither encodes nor calls the API, and the size of what
    it will render is bounded by `ReportIn` rather than by a budget.

    Whose report it is comes from the token, never from the body: the header names the
    caller and the organization or unit they sit in, resolved as `GET /auth/me` does,
    so a user cannot issue a report in somebody else's name.
    """
    organization = organization_of(db, user)
    # Rendering is blocking and CPU-bound (font shaping, layout, PDF writing)
    pdf = await run_in_threadpool(
        render_pdf, body, user,
        organization.name if organization else None,
        user.unit.name if user.unit else None,
    )
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename()}"'},
    )
