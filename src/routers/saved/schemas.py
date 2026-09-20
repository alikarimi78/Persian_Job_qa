from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

# A starred search is the answer the reader saw, posted back by the client the way
# `reports` posts one to be printed — so it is untrusted input, not a round-trip of our
# own model, and every string is bounded. Identity is never read from the body: the row
# belongs to the token's account.
SavedText = Annotated[str, StringConstraints(max_length=20_000)]
SavedLabel = Annotated[str, StringConstraints(max_length=255)]


class SavedFieldIn(BaseModel):
    key: SavedLabel
    label: SavedLabel
    value: SavedText = ""
    items: list[SavedText] = Field(default=[], max_length=500)
    primary: bool = False
    preview: int = 0


class SavedDetailIn(BaseModel):
    job_title: SavedLabel
    fields: list[SavedFieldIn] = Field(default=[], max_length=40)


# The `SearchOut` the client holds, bounded. Unknown keys are dropped rather than stored,
# so a client one version ahead cannot fill the row with anything the reader will not see.
class SavedResultIn(BaseModel):
    mode: SavedLabel
    intent: SavedLabel = ""
    answer: SavedText
    job: SavedLabel | None = None
    jobs: list[SavedLabel] | None = Field(default=None, max_length=8)
    score: float | None = None
    scores: list[float] | None = Field(default=None, max_length=8)
    details: list[SavedDetailIn] | None = Field(default=None, max_length=4)
    related_jobs: list[SavedLabel] | None = Field(default=None, max_length=20)
    nearest: SavedDetailIn | None = None
    organization_id: int | None = None
    job_draft: dict[SavedLabel, SavedText] | None = None
    draft_detail: SavedDetailIn | None = None
    draft_reason: SavedLabel | None = None
    draft_job: SavedLabel | None = None


class SavedSearchIn(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    result: SavedResultIn


# The listing carries what a row is recognised by; the answer itself rides only on the
# one row a reader opens, a page of twenty answers being some 300 KB of prose.
class SavedSearchOut(BaseModel):
    id: int
    question: str
    mode: str
    job_title: str | None = None
    created_at: datetime
    updated_at: datetime


class SavedSearchDetailOut(SavedSearchOut):
    result: SavedResultIn


class SavedPage(BaseModel):
    items: list[SavedSearchOut]
    total: int
    page: int
    page_size: int
