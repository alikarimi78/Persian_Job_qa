from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

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


class SavedResultIn(BaseModel):
    mode: SavedLabel
    intent: SavedLabel = ""
    answer: SavedText
    job: SavedLabel | None = None
    jobs: list[SavedLabel] | None = Field(default=None, max_length=8)
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
