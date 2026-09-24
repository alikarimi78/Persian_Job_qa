from fastapi import HTTPException, status
from prisma import Prisma
from prisma.partials import JobTitleRow
from prisma.types import JobRecordWhereInput

from src.models import JobRecord, JobStatus, Role, User
from src.permissions import assert_can_admit_job
from src.routers.jobs.schemas import JobIn

JOBS_PAGE_SIZE = 20
JOBS_PAGE_MAX = 100

_ZWNJ = "\u200c"


def _record(db: Prisma, job_id: int, actor: User) -> JobRecord:
    record = db.jobrecord.find_unique(where={"id": job_id})
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Record not found")
    assert_can_admit_job(actor, record.organization_id)
    return record


def pending(db: Prisma, job_id: int, actor: User) -> JobRecord:
    record = _record(db, job_id, actor)
    if record.status != JobStatus.pending:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Record is already {record.status}")
    return record


def approved(db: Prisma, job_id: int, actor: User) -> JobRecord:
    record = _record(db, job_id, actor)
    if record.status != JobStatus.approved:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Record is {record.status}; a suggestion is edited through /admin/suggestions")
    return record


def review(db: Prisma, job_id: int, new_status: JobStatus, admin: User) -> JobRecord:
    pending(db, job_id, admin)
    return db.jobrecord.update(
        where={"id": job_id},
        data={"status": new_status, "reviewer": {"connect": {"id": admin.id}}})


def target_owner(body: JobIn, record: JobRecord) -> int | None:
    return (body.organization_id if "organization_id" in body.model_fields_set
            else record.organization_id)


def update_data(body: JobIn, organization_id: int | None) -> dict:
    data = body.model_dump()
    data.pop("organization_id")
    data["organization"] = ({"connect": {"id": organization_id}}
                            if organization_id is not None else {"disconnect": True})
    return data


def organization_filter(actor: User, organization_id: int | None, public: bool,
                        reach: set[int | None] | None = None) -> JobRecordWhereInput:
    if public and organization_id is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Filter by organization_id or by public, not both")
    asked: JobRecordWhereInput = {}
    if public:
        asked = {"organization_id": None}
    elif organization_id is not None:
        asked = {"organization_id": organization_id}

    if actor.role == Role.super_admin:
        return asked
    owners = reach if reach is not None else {actor.organization_id}
    scope: JobRecordWhereInput = {"OR": [{"organization_id": owner} for owner in owners]}
    return scope if not asked else {"AND": [scope, asked]}


_FOLD = str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک", "ؤ": "و", "ئ": "ی", "أ": "ا", "إ": "ا",
                       "ٱ": "ا", "ة": "ه", "ۀ": "ه", _ZWNJ: " ",
                       **{chr(mark): None for mark in range(0x064B, 0x0671)}})


def fold_title(text: str) -> str:
    return " ".join(str(text or "").lower().translate(_FOLD).split())


def matching_ids(db: Prisma, where: JobRecordWhereInput, query: str) -> list[int]:
    folded = fold_title(query)
    rows = JobTitleRow.prisma(db).find_many(where=where, order={"job_title": "asc"})
    return [row.id for row in rows if folded in fold_title(row.job_title)]
