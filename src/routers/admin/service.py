from fastapi import HTTPException, status
from prisma import Prisma

from src.models import JobRecord, JobStatus, User

JOBS_PAGE_SIZE = 20
JOBS_PAGE_MAX = 100

_ZWNJ = "\u200c"


# `pending` guards a record no search can reach yet and `approved` guards the corpus
# everyone searches; each answers 409 for the other's records, which is why the two
# edit endpoints are deliberately not one widened endpoint.
def pending(db: Prisma, job_id: int) -> JobRecord:
    record = db.jobrecord.find_unique(where={"id": job_id})
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Record not found")
    if record.status != JobStatus.pending:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Record is already {record.status}")
    return record


def approved(db: Prisma, job_id: int) -> JobRecord:
    record = db.jobrecord.find_unique(where={"id": job_id})
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Record not found")
    if record.status != JobStatus.approved:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Record is {record.status}; a suggestion is edited through /admin/suggestions")
    return record


def review(db: Prisma, job_id: int, new_status: JobStatus, admin: User) -> JobRecord:
    pending(db, job_id)
    return db.jobrecord.update(
        where={"id": job_id},
        data={"status": new_status, "reviewer": {"connect": {"id": admin.id}}})


# `contains` is literal and the corpus is hazm-normalized, so the row says
# «برنامه‌نویسان» while an admin types «برنامه نویسان»: OR the query with its
# space↔ZWNJ variants, folding Arabic ي/ك onto Persian ی/ک first.
def title_filters(query: str) -> list[dict]:
    query = query.strip().replace("ي", "ی").replace("ك", "ک")
    forms = {query, query.replace(" ", _ZWNJ), query.replace(_ZWNJ, " ")}
    return [{"job_title": {"contains": form, "mode": "insensitive"}}
            for form in forms if form]
