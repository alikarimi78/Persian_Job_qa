from fastapi import APIRouter, Depends
from prisma import Prisma

from src.database import get_db
from src.models import User
from src.security import get_current_user

from .schemas import SavedPage, SavedSearchDetailOut, SavedSearchIn, SavedSearchOut
from .service import SAVED_PAGE_MAX, SAVED_PAGE_SIZE, owned, save

# A reader's own stars, and nobody else's: there is no listing across accounts and no admin
# view of one, a saved search being what somebody looked up rather than a record of the system.
router = APIRouter(prefix="/saved", tags=["saved"],
                   dependencies=[Depends(get_current_user)])


@router.post("", response_model=SavedSearchOut, status_code=201)
def save_search(body: SavedSearchIn, actor: User = Depends(get_current_user),
                db: Prisma = Depends(get_db)):
    return save(db, actor, body)


# Newest first — a star is looked up again soon after it is set. `page`/`page_size` are clamped
# rather than validated, as the corpus listing's are: a 422 would take the panel down.
@router.get("", response_model=SavedPage)
def list_saved(page: int = 1, page_size: int = SAVED_PAGE_SIZE,
               actor: User = Depends(get_current_user), db: Prisma = Depends(get_db)):
    page = max(page, 1)
    page_size = min(max(page_size, 1), SAVED_PAGE_MAX)
    where = {"user_id": actor.id}
    return {"items": db.savedsearch.find_many(where=where, skip=(page - 1) * page_size,
                                              take=page_size, order={"created_at": "desc"}),
            "total": db.savedsearch.count(where=where),
            "page": page, "page_size": page_size}


@router.get("/{saved_id}", response_model=SavedSearchDetailOut)
def read_saved(saved_id: int, actor: User = Depends(get_current_user),
               db: Prisma = Depends(get_db)):
    row = owned(db, saved_id, actor)
    return {**row.model_dump(), "result": row.payload}


@router.delete("/{saved_id}", status_code=204)
def delete_saved(saved_id: int, actor: User = Depends(get_current_user),
                 db: Prisma = Depends(get_db)):
    owned(db, saved_id, actor)
    db.savedsearch.delete(where={"id": saved_id})
