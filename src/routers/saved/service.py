from fastapi import HTTPException, status
from prisma import Json, Prisma

from src.models import SavedSearch, User

from .schemas import SavedSearchIn

SAVED_PAGE_SIZE = 20
SAVED_PAGE_MAX = 100
SAVED_MAX = 200


def owned(db: Prisma, saved_id: int, actor: User) -> SavedSearch:
    row = db.savedsearch.find_first(where={"id": saved_id, "user_id": actor.id})
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Saved search not found")
    return row


def result_title(body: SavedSearchIn) -> str | None:
    result = body.result
    if result.job:
        return result.job
    if result.details:
        return result.details[0].job_title
    return None


def save(db: Prisma, actor: User, body: SavedSearchIn) -> SavedSearch:
    question = body.question.strip()
    data = {"mode": body.result.mode, "job_title": result_title(body),
            "payload": Json(body.result.model_dump())}

    existing = db.savedsearch.find_first(where={"user_id": actor.id, "question": question})
    if existing is not None:
        return db.savedsearch.update(where={"id": existing.id}, data=data)

    if db.savedsearch.count(where={"user_id": actor.id}) >= SAVED_MAX:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Saved searches are full ({SAVED_MAX}); remove one before adding another")
    return db.savedsearch.create(
        data={**data, "question": question, "user": {"connect": {"id": actor.id}}})
