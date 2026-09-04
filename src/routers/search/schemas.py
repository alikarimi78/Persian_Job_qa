from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, field_validator

# The searchable profile columns, a copy of `job_qa_service.columns.PROFILE_FIELDS`. The
# copy exists because `tests/conftest.py` stubs the engine away, so this module cannot
# import it; the frontend's `search/AdvancedSearch.jsx:FIELDS` is the third copy.
PROFILE_FIELDS = ["skills", "knowledge", "abilities", "responsibilities",
                  "work_context", "career_path_next"]
PROFILE_REQUIRED_FIELD = "skills"
PROFILE_MIN_ITEMS = 2
PROFILE_MIN_FIELDS = 2
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
    score: float | None = None
    scores: list[float] | None = None
    details: list[JobDetailOut] | None = None
    related_jobs: list[str] | None = None
    job_draft: dict[str, str] | None = None


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

        required = cleaned.get(PROFILE_REQUIRED_FIELD, [])
        if len(required) < PROFILE_MIN_ITEMS:
            raise ValueError(
                f"{PROFILE_REQUIRED_FIELD}: at least {PROFILE_MIN_ITEMS} items are required")
        if len(cleaned) < PROFILE_MIN_FIELDS:
            raise ValueError(f"At least {PROFILE_MIN_FIELDS} fields must be filled in")
        return cleaned


class ProfileFieldOut(BaseModel):
    key: str
    label: str
    matched: list[str] = []
    missing: list[str] = []
    ratio: float


class ProfileMatchOut(BaseModel):
    job_title: str
    score: float
    dense: float
    coverage: float
    fields: list[ProfileFieldOut] = []
    detail: JobDetailOut


class ProfileSearchOut(BaseModel):
    mode: str
    intent: str
    answer: str
    job: str | None = None
    score: float | None = None
    matches: list[ProfileMatchOut] = []
