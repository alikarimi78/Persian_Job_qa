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
