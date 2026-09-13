from fastapi import HTTPException, status
from prisma import Prisma
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


# `pending` guards a record no search can reach yet and `approved` guards the corpus
# everyone searches; each answers 409 for the other's records, which is why the two
# edit endpoints are deliberately not one widened endpoint. Both go through the reach
# check first, so no /admin path can act on another organization's record.
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


# An edit that does not mention the owner leaves it alone, an explicit null returns the
# record to the public corpus — the same rule `OrganizationUpdateIn` follows, and here it
# is what stops a client that predates the column from handing one organization's record
# to every other by saving an unrelated column.
def target_owner(body: JobIn, record: JobRecord) -> int | None:
    return (body.organization_id if "organization_id" in body.model_fields_set
            else record.organization_id)


# `prisma generate` leaves the foreign key out of an update input, so a change of owner
# travels as the relation: connect names an organization, disconnect returns the record
# to the public corpus. A create takes the plain `organization_id` and needs none of it.
def update_data(body: JobIn, organization_id: int | None) -> dict:
    data = body.model_dump()
    data.pop("organization_id")
    data["organization"] = ({"connect": {"id": organization_id}}
                            if organization_id is not None else {"disconnect": True})
    return data


# What this admin may list, and the filter asked for on top of it. An org_admin is
# confined to their own organization's records; a super_admin sees every one and may
# narrow to a single organization, or to the public corpus, which `organization_id`
# alone cannot name — it is the absence of one.
def organization_filter(actor: User, organization_id: int | None,
                        public: bool) -> JobRecordWhereInput:
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
    scope: JobRecordWhereInput = {"organization_id": actor.organization_id}
    return scope if not asked else {"AND": [scope, asked]}


# `contains` is literal and the corpus is hazm-normalized, so the row says
# «برنامه‌نویسان» while an admin types «برنامه نویسان»: OR the query with its
# space↔ZWNJ variants, folding Arabic ي/ك onto Persian ی/ک first.
def title_filters(query: str) -> list[dict]:
    query = query.strip().replace("ي", "ی").replace("ك", "ک")
    forms = {query, query.replace(" ", _ZWNJ), query.replace(_ZWNJ, " ")}
    return [{"job_title": {"contains": form, "mode": "insensitive"}}
            for form in forms if form]
