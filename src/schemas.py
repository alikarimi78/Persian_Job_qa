import re
from datetime import datetime
from typing import Annotated

from pydantic import (BaseModel, Field, ConfigDict, StringConstraints, computed_field,
                      field_validator)

from .models import join_name


class LoginIn(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str


_MAX_LOGO_URI = 800_000

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
_PASSWORD_SPECIAL_RE = re.compile(r"[^A-Za-z0-9]")


def _validate_password_strength(value: str) -> str:
    if (not re.search(r"[a-z]", value) or not re.search(r"[A-Z]", value)
            or not _PASSWORD_SPECIAL_RE.search(value)):
        raise ValueError("Password must contain an uppercase letter, a lowercase "
                         "letter, and a special character (e.g. @)")
    return value


_DIGIT_MAP = {ord(c): str(i % 10) for i, c in enumerate("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩")}


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


class OrganizationProfile(BaseModel):
    code: str | None = Field(default=None, max_length=64)
    address: str | None = Field(default=None, max_length=512)
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=254)
    logo: str | None = Field(default=None, max_length=_MAX_LOGO_URI)

    @field_validator("code", "address")
    @classmethod
    def _clean(cls, value):
        return _blank_to_none(value)

    @field_validator("phone")
    @classmethod
    def _clean_phone(cls, value):
        value = _blank_to_none(value)
        if value is None:
            return None
        value = value.translate(_DIGIT_MAP)
        if not re.fullmatch(r"[0-9+\-() ]{7,32}", value) or sum(c.isdigit() for c in value) < 7:
            raise ValueError("Phone must be 7-20 digits, optionally with + - ( ) or spaces")
        return value

    @field_validator("email")
    @classmethod
    def _clean_email(cls, value):
        value = _blank_to_none(value)
        if value is None:
            return None
        value = value.lower()
        if not _EMAIL_RE.match(value):
            raise ValueError("Not a valid email address")
        return value


class OrganizationIn(OrganizationProfile):
    name: str = Field(min_length=2, max_length=128)


class OrganizationUpdateIn(OrganizationProfile):
    name: str | None = Field(default=None, min_length=2, max_length=128)


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    code: str | None = None
    address: str | None = None
    phone: str | None = None
    email: str | None = None
    logo_mime: str | None = Field(default=None, exclude=True)

    @computed_field
    @property
    def has_logo(self) -> bool:
        return self.logo_mime is not None


class OrganizationLogoOut(BaseModel):
    logo: str | None = None


class NameIn(BaseModel):
    first_name: str = Field(min_length=1, max_length=64)
    last_name: str = Field(min_length=1, max_length=64)

    @field_validator("first_name", "last_name")
    @classmethod
    def _clean(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name cannot be blank")
        return value


class AccountIn(NameIn):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def _password_strength(cls, value: str) -> str:
        return _validate_password_strength(value)


class OrgAdminIn(AccountIn):
    organization_id: int


class UserAccountIn(AccountIn):
    organization_id: int | None = None


class MoveOrganizationIn(BaseModel):
    organization_id: int


class PasswordResetIn(BaseModel):
    password: str = Field(min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def _password_strength(cls, value: str) -> str:
        return _validate_password_strength(value)


class SelfPasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _password_strength(cls, value: str) -> str:
        return _validate_password_strength(value)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    first_name: str | None = None
    last_name: str | None = None
    role: str
    is_active: bool = True
    organization_id: int | None = None

    @computed_field
    @property
    def full_name(self) -> str | None:
        return join_name(self.first_name, self.last_name)


class MeOut(UserOut):
    organization: OrganizationOut | None = None


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


ReportText = Annotated[str, StringConstraints(max_length=20_000)]
ReportLabel = Annotated[str, StringConstraints(max_length=255)]


class ReportFieldIn(BaseModel):
    key: ReportLabel
    label: ReportLabel
    value: ReportText = ""
    items: list[ReportText] = Field(default=[], max_length=500)
    primary: bool = False


class ReportJobIn(BaseModel):
    job_title: ReportLabel
    fields: list[ReportFieldIn] = Field(default=[], max_length=40)


class ReportIn(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    mode: ReportLabel
    answer: ReportText
    job: ReportLabel | None = None
    jobs: list[ReportLabel] | None = Field(default=None, max_length=8)
    details: list[ReportJobIn] = Field(default=[], max_length=4)
    related_jobs: list[ReportLabel] | None = Field(default=None, max_length=20)


PROFILE_FIELDS = ["skills", "knowledge", "abilities", "responsibilities",
                  "work_context", "career_path_next"]
PROFILE_REQUIRED_FIELD = "skills"
PROFILE_MIN_ITEMS = 2
PROFILE_MIN_FIELDS = 2
PROFILE_MAX_ITEMS = 20

ProfileItem = Annotated[str, StringConstraints(max_length=120)]


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


class JobIn(BaseModel):
    job_title: str = Field(min_length=2, max_length=255)
    aliases: str = Field(min_length=1)
    tools: str = Field(min_length=1)
    skills: str = Field(min_length=1)
    knowledge: str = Field(min_length=1)
    abilities: str = Field(min_length=1)
    work_context: str = Field(min_length=1)
    career_path_next: str = Field(min_length=1)
    description: str = Field(min_length=1)
    responsibilities: str = Field(min_length=1)


class JobOut(JobIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    status: str
    suggested_by: int | None
    updated_at: datetime | None = None


for _column in JobIn.model_fields:
    JobOut.model_fields[_column].metadata = []
JobOut.model_rebuild(force=True)


class JobPage(BaseModel):
    items: list[JobOut]
    total: int
    page: int
    page_size: int


class RebuildStatus(BaseModel):
    running: bool
    last_result: str | None


class SeriesPoint(BaseModel):
    date: str
    count: int


class RoleCount(BaseModel):
    role: str
    count: int


class JobStats(BaseModel):
    corpus_records: int
    engine_records: int | None
    pending: int
    approved: int
    rejected: int


class StatsOut(BaseModel):
    scope: str
    scope_name: str | None
    organizations: int
    accounts: int
    accounts_active: int
    accounts_blocked: int
    accounts_by_role: list[RoleCount]
    jobs: JobStats
    accounts_series: list[SeriesPoint]
    organizations_series: list[SeriesPoint]
    suggestions_series: list[SeriesPoint]
