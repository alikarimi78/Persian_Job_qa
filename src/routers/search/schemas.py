from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, field_validator

PROFILE_FIELDS = ["skills", "knowledge", "abilities", "responsibilities",
                  "work_context", "career_path_next", "tools"]
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
    organization_id: int | None = None
    job_draft: dict[str, str] | None = None
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
    unknown: list[str] = []
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


class VocabularyOut(BaseModel):
    fields: dict[str, list[VocabularyItemOut]]
