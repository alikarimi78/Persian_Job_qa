from fastapi import APIRouter, Depends, HTTPException, status
from prisma import Prisma

from ..auth import require_super_admin
from ..database import get_db
from ..engine_manager import manager
from ..models import User, JobRecord, JobStatus
from ..schemas import JobIn, JobOut, JobPage, RebuildStatus

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_super_admin)])


@router.get("/suggestions", response_model=list[JobOut])
def list_suggestions(job_status: JobStatus = JobStatus.pending,
                     db: Prisma = Depends(get_db)):
    return db.jobrecord.find_many(where={"status": job_status})


def _pending(db: Prisma, job_id: int) -> JobRecord:
    """The record, if it is still open to review. Both of the two edits below are
    confined to `pending` and answer the same 409, so the pair of checks is one function
    rather than two copies."""
    record = db.jobrecord.find_unique(where={"id": job_id})
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Record not found")
    if record.status != JobStatus.pending:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Record is already {record.status}")
    return record


def _review(job_id: int, new_status: JobStatus, admin: User, db: Prisma) -> JobRecord:
    _pending(db, job_id)
    # `reviewer: connect`, not a bare `reviewed_by`: Prisma's update inputs take the
    # relation rather than the foreign key column (`create` accepts either). The row that
    # comes back is what the response is built from, so nothing re-reads it.
    return db.jobrecord.update(
        where={"id": job_id},
        data={"status": new_status, "reviewer": {"connect": {"id": admin.id}}})


@router.put("/suggestions/{job_id}", response_model=JobOut)
def edit_suggestion(job_id: int, body: JobIn, db: Prisma = Depends(get_db)):
    """An admin corrects a pending suggestion before deciding on it.

    The whole record is sent, not a patch: the reviewer edits the same ten-column form
    the suggester filled in, so a partial body would only add a way for a column to go
    missing. `JobIn` is what keeps every column filled either way.

    Editing is deliberately confined to `pending`. An approved record is part of the one
    corpus every organization searches — changing it there is a dataset edit that also
    needs a rebuild before the search reflects it — and a rejected one is closed. Both
    answer 409, the same way a second review does."""
    _pending(db, job_id)
    return db.jobrecord.update(where={"id": job_id}, data=body.model_dump())


@router.post("/suggestions/{job_id}/approve", response_model=JobOut)
def approve(job_id: int, admin: User = Depends(require_super_admin), db: Prisma = Depends(get_db)):
    """Approving also **starts the rebuild**, so the record is searchable without a second
    decision. It is fired after `_review` has written the row — the rebuild reads the
    approved rows in a query of its own, and Prisma commits each write on its own, so the
    ordering here is what makes the new record visible to it.

    Nothing is awaited: `rebuild_async` swaps the new engine in on a daemon thread while
    the old one keeps serving, and its progress is reported by `/admin/rebuild/status`
    exactly as it is for the button. A `False` here means a rebuild was already running
    and this one is queued behind it, which is not an error to report."""
    record = _review(job_id, JobStatus.approved, admin, db)
    manager.rebuild_async()
    return record


@router.post("/suggestions/{job_id}/reject", response_model=JobOut)
def reject(job_id: int, admin: User = Depends(require_super_admin), db: Prisma = Depends(get_db)):
    return _review(job_id, JobStatus.rejected, admin, db)


@router.post("/jobs", response_model=JobOut, status_code=201)
def create_job(body: JobIn, admin: User = Depends(require_super_admin), db: Prisma = Depends(get_db)):
    """A super_admin adds a record directly; it is approved immediately — and rebuilt
    immediately, for the same reason approving a suggestion is: this inserts an approved
    row, and an approved row nobody can find is the same problem either way."""
    record = db.jobrecord.create(data={**body.model_dump(), "status": JobStatus.approved,
                                       "suggested_by": admin.id, "reviewed_by": admin.id})
    manager.rebuild_async()
    return record


# ---------- the corpus itself ----------
#
# Everything above this line is about a record on its way *into* the corpus. These two
# are about the records already in it, which is a different job with a different guard:
# `_pending` protects a review, `_approved` protects a dataset edit.

JOBS_PAGE_SIZE = 20
JOBS_PAGE_MAX = 100

_ZWNJ = "\u200c"


def _title_filters(query: str) -> list[dict]:
    """Every spelling of what was typed, as an OR over `job_title`.

    `contains` is a literal substring match, and Persian compounds are stored with a
    ZWNJ in them — hazm's normalizer inserts it and the seed ran the corpus through it,
    so the row says «برنامه‌نویسان». An admin types «برنامه نویس» with a space and would
    otherwise be told the corpus holds no such record. The Arabic ي/ك are folded for the
    same reason: they arrive from a paste and are a different codepoint from the Persian
    letters the corpus is normalized to.
    """
    query = query.strip().replace("ي", "ی").replace("ك", "ک")
    forms = {query, query.replace(" ", _ZWNJ), query.replace(_ZWNJ, " ")}
    return [{"job_title": {"contains": form, "mode": "insensitive"}}
            for form in forms if form]


@router.get("/jobs", response_model=JobPage)
def list_jobs(q: str = "", page: int = 1, page_size: int = JOBS_PAGE_SIZE,
              job_status: JobStatus = JobStatus.approved, db: Prisma = Depends(get_db)):
    """One page of the corpus, searched by title.

    Paginated because the corpus is 1118 records and each is ~4.5 KB of text since the
    retranslation — whole, it is a 5 MB response. Ordered by title rather than by id, so
    the page someone lands on is the same page tomorrow and so browsing groups the
    families of an occupation together.

    Defaults to `approved`, which is the only status this panel is about: `pending`
    belongs to the moderation queue above and `rejected` is closed. The parameter is
    still offered because the filter costs nothing and a rejected record is occasionally
    worth looking up.

    `page` and `page_size` are clamped rather than validated: a pager that asks for page
    0 has a bug in it, and answering 422 would take the panel down over an off-by-one.
    """
    page = max(page, 1)
    page_size = min(max(page_size, 1), JOBS_PAGE_MAX)
    where: dict = {"status": job_status}
    if q.strip():
        where["OR"] = _title_filters(q)

    # Two queries and not one: Prisma has no windowed count, and `total` is what the
    # pager is drawn from — the page alone cannot say how many there are.
    total = db.jobrecord.count(where=where)
    items = db.jobrecord.find_many(where=where, skip=(page - 1) * page_size,
                                   take=page_size, order={"job_title": "asc"})
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def _approved(db: Prisma, job_id: int) -> JobRecord:
    """The record, if it is part of the corpus — the counterpart of `_pending` above."""
    record = db.jobrecord.find_unique(where={"id": job_id})
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Record not found")
    if record.status != JobStatus.approved:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Record is {record.status}; a suggestion is edited through /admin/suggestions")
    return record


@router.put("/jobs/{job_id}", response_model=JobOut)
def edit_job(job_id: int, body: JobIn, db: Prisma = Depends(get_db)):
    """A super_admin corrects a record that is already in the corpus — **and the engine
    is rebuilt at once**, which is the whole reason this is not the same endpoint as the
    one above it.

    It is the third path that changes what a search can reach, next to approving a
    suggestion and adding a record directly, and it starts a rebuild for exactly the
    reason those two do: a correction the search still answers from the old wording is
    the same problem as an approved record nobody can find. The call sits after the
    write — Prisma commits each one on its own and the rebuild reads the approved rows
    in a query of its own, so that ordering is what makes the new text visible to it.
    Nothing is awaited, and a `False` (a rebuild already running) is ignored because the
    queued pass covers this record.

    It costs what an approval costs, not what a re-encode costs: the embedding store is
    keyed on `sha256(model + text)`, so this record's own two texts are the only ones
    that miss — and the title/alias one only if the title or the aliases changed — while
    the other ~2230 are read from the store.

    Confined to `approved`, which is the whole difference between this and
    `PUT /admin/suggestions/{id}`: that one is a review action on a record no search can
    reach yet, this one is a dataset edit. Neither does the other's job and both answer
    409, rather than one of them quietly widening to cover every status.

    `reviewed_by` is deliberately left alone. It records who admitted the record to the
    corpus; an edit afterwards is not a second admission, and overwriting it would lose
    the only trace of who made the decision.
    """
    _approved(db, job_id)
    record = db.jobrecord.update(where={"id": job_id}, data=body.model_dump())
    manager.rebuild_async()
    return record


@router.post("/rebuild", status_code=202)
def rebuild(force_embeddings: bool = False):
    """Rebuilds the engine from approved records in the background.
    Search keeps serving the old engine until the new one atomically swaps in.

    Only the records whose text the embedding store has never seen are encoded, so
    this is seconds after an approval rather than a full re-encode. `force_embeddings`
    re-encodes the whole corpus and overwrites the store — minutes on GPU, ~31 min on
    CPU — and is only for a store that has gone bad."""
    if not manager.rebuild_async(force_embeddings=force_embeddings):
        raise HTTPException(status.HTTP_409_CONFLICT, "A rebuild is already running")
    return {"detail": "Rebuild started"}


@router.get("/rebuild/status", response_model=RebuildStatus)
def rebuild_status():
    return RebuildStatus(running=manager.rebuilding, last_result=manager.last_result)
