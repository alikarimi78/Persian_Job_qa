from fastapi import APIRouter, Depends, Response
from fastapi.concurrency import run_in_threadpool
from prisma import Prisma

from src.database import get_db
from src.models import User
from src.security import get_current_user
from src.routers.accounts.service import organization_of

from .render import filename, render_pdf
from .schemas import ReportIn

router = APIRouter(prefix="/reports", tags=["reports"])


# The client posts back the answer it already holds and the server does not re-run the
# search, the model not being deterministic. Identity is never taken from the body —
# the masthead's user and organization come from the token.
@router.post(
    "/search",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}},
                     "description": "The report, as a PDF file"}},
)
async def search_report(body: ReportIn, user: User = Depends(get_current_user),
                        db: Prisma = Depends(get_db)):
    organization = organization_of(db, user)
    pdf = await run_in_threadpool(
        render_pdf, body, user,
        organization.name if organization else None,
    )
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename()}"'},
    )
