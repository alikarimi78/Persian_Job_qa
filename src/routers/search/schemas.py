from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, field_validator

# The searchable profile columns, a copy of `job_qa_service.columns.PROFILE_FIELDS`. The
# copy exists because `tests/conftest.py` stubs the engine away, so this module cannot
# import it; the frontend's `search/AdvancedSearch.jsx:FIELDS` is the third copy.
PROFILE_FIELDS = ["skills", "knowledge", "abilities", "responsibilities",
                  "work_context", "career_path_next", "tools"]
# What a profile must carry, and how many items each field needs. `skills` is the spine of
# the ranking — O*NET's ten basic skills, which every record has — and the three beside it
# are what tell two jobs with the same skills apart; a profile of skills alone ranked far
# too many of them equally well. `responsibilities` and `career_path_next` stay optional:
# the first is free wording rather than a vocabulary, the second is where the reader wants
# to go rather than what they can do. They carry the whole rule, so there is no separate
# "at least N fields" check any more.
PROFILE_REQUIRED = {"skills": 2, "knowledge": 1, "abilities": 1, "work_context": 1}
PROFILE_MAX_ITEMS = 20

ProfileItem = Annotated[str, StringConstraints(max_length=120)]


class SearchIn(BaseModel):
    question: str = Field(min_length=1, max_length=500)


class JobFieldOut(BaseModel):
    key: str
    label: str
    value: str
    items: list[str] = []
    primary: bool = False
    preview: int = 0


class JobDetailOut(BaseModel):
    job_title: str
    fields: list[JobFieldOut]


class SearchOut(BaseModel):
    mode: str
    intent: str
    answer: str
    job: str | None = None
    jobs: list[str] | None = None
    details: list[JobDetailOut] | None = None
    related_jobs: list[str] | None = None
    nearest: JobDetailOut | None = None
    # The owner of the stored record a `single`/`job_match` answered from; None is public.
    organization_id: int | None = None
    job_draft: dict[str, str] | None = None
    # A combination's offer: the job composed beside its two records and that job's boxes, or
    # why there is none — `exists` (the stored job in `draft_job`), `not_a_job`, `too_vague`,
    # `unavailable`.
    draft_detail: JobDetailOut | None = None
    draft_reason: str | None = None
    draft_job: str | None = None


class ProfileSearchIn(BaseModel):
    profile: dict[str, list[ProfileItem]]

    @field_validator("profile")
    @classmethod
    def _check(cls, profile):
        unknown = [key for key in profile if key not in PROFILE_FIELDS]
        if unknown:
            raise ValueError("Unknown profile fields: " + ", ".join(sorted(unknown)))

        cleaned = {}
        for key, items in profile.items():
            if len(items) > PROFILE_MAX_ITEMS:
                raise ValueError(f"{key}: at most {PROFILE_MAX_ITEMS} items")
            kept = [item.strip() for item in items if item.strip()]
            if kept:
                cleaned[key] = kept

        for field, minimum in PROFILE_REQUIRED.items():
            if len(cleaned.get(field, [])) < minimum:
                raise ValueError(f"{field}: at least {minimum} items are required")
        return cleaned


class ProfileFieldOut(BaseModel):
    key: str
    label: str
    matched: list[str] = []
    missing: list[str] = []
    # Items no record in reach holds in any column: the corpus has no word for them, so they are
    # neither covered nor missing and count toward neither ratio.
    unknown: list[str] = []
    # A matched item found outside the column it was typed in, and which column that was.
    found_in: dict[str, str] = {}
    ratio: float


class ProfileMatchOut(BaseModel):
    job_title: str
    coverage: float
    fields: list[ProfileFieldOut] = []
    detail: JobDetailOut


class ProfileSearchOut(BaseModel):
    mode: str
    intent: str
    answer: str
    job: str | None = None
    matches: list[ProfileMatchOut] = []


class VocabularyItemOut(BaseModel):
    text: str
    count: int


# The phrases advanced analysis offers while typing, per field, most common first.
class VocabularyOut(BaseModel):
    fields: dict[str, list[VocabularyItemOut]]
