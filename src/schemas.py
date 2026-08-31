import re
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


# ---------- tenancy: organizations and units ----------
# A data URI is ~4/3 of the bytes it carries, so this caps the encoded string a little
# above `MAX_LOGO_BYTES` (routers/orgs.py) — a cheap first guard, so an oversized upload
# is refused by the parser instead of being base64-decoded first.
_MAX_LOGO_URI = 800_000

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
# Persian and Arabic-Indic digits both reach us from an Iranian keyboard; the column
# stores ASCII so that two records typed on different layouts are the same phone number.
# The client renders them back to Persian digits with `faNumber`, as it does everywhere.
_DIGIT_MAP = {ord(c): str(i % 10) for i, c in enumerate("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩")}


def _blank_to_none(value: str | None) -> str | None:
    """An empty box in the form means «clear this», not «store an empty string»."""
    if value is None:
        return None
    value = value.strip()
    return value or None


class OrganizationProfile(BaseModel):
    """The contact detail an organization carries, from the customer's admin_panel.mp4.

    Every one of these is optional *here* even though the reference form marks all but
    the code as required. Two reasons, and they are the same reason twice: the
    organizations that predate this migration have none of it, and `PATCH` is how they
    get fixed — an admin correcting a name must not be told to invent an email first.
    Requiring them is the form's job (`components/manage/Forms.jsx`), where the person
    filling it in can see which box is empty.
    """
    code: str | None = Field(default=None, max_length=64)
    address: str | None = Field(default=None, max_length=512)
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=254)
    # The image itself, as a `data:image/png;base64,…` URI. It travels as a string
    # because multipart would mean adding python-multipart for this one endpoint, and
    # because the client already holds the file as a data URI (FileReader). Decoded and
    # checked in `routers/orgs.py:decode_logo`; `None` leaves an existing logo alone,
    # while `""` removes it.
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
    """`PATCH /orgs/{id}`. Every field is optional and only the ones actually sent are
    applied (`exclude_unset`), so a client may send the whole form or one box of it.
    Sending a field as `""` or `null` is how a value is *cleared* — which is why absent
    and empty have to stay distinguishable, and why this is not just OrganizationIn with
    a default name."""
    name: str | None = Field(default=None, min_length=2, max_length=128)


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    code: str | None = None
    address: str | None = None
    phone: str | None = None
    email: str | None = None
    # Not the logo itself. `GET /orgs` is read by three pages on every visit, and a list
    # carrying one image per row would cost megabytes for a column most of them only
    # need to know is *there* — the thumbnail asks for it separately.
    #
    # `has_logo` is computed here rather than read off the row because a Prisma model is
    # regenerated from `schema.prisma` on every `prisma generate` and cannot carry a
    # property of its own — the answer has to be derived somewhere that survives, and
    # this type is what every organization response goes through. `logo_mime` is carried
    # only to compute it and is excluded from the response.
    logo_mime: str | None = Field(default=None, exclude=True)

    @computed_field
    @property
    def has_logo(self) -> bool:
        return self.logo_mime is not None


class OrganizationLogoOut(BaseModel):
    """`GET /orgs/{id}/logo` — the image as a data URI, or null if there is none.

    A data URI and not the raw bytes, because the endpoint needs the Authorization
    header like every other one and an `<img src>` cannot send it. This way the client
    fetches it through the same RTK Query base as everything else and drops the string
    straight into `src`."""
    logo: str | None = None


class UnitIn(BaseModel):
    name: str = Field(min_length=2, max_length=128)
    # Required of a super_admin, who has no organization of their own to default to;
    # an org_admin may omit it, and may not name any organization but their own.
    organization_id: int | None = None


class UnitOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    organization_id: int


class RenameIn(BaseModel):
    """The whole of what `PATCH /units/{id}` accepts. A unit is its name plus what it
    holds, and everything it holds moves through its own endpoint — it never changes
    organization here and never changes id.

    Organizations outgrew this when they gained a profile (`OrganizationUpdateIn`); a
    unit has not, and giving it the same partial-update shape would only add a way to
    send nothing at all."""
    name: str = Field(min_length=2, max_length=128)


# ---------- accounts ----------
class NameIn(BaseModel):
    """`POST /accounts/{id}/name` and `POST /auth/name`. Both parts together, never one
    at a time: a half-changed name is not a correction anyone asked for, and the pair is
    what the PDF prints."""
    first_name: str = Field(min_length=1, max_length=64)
    last_name: str = Field(min_length=1, max_length=64)

    @field_validator("first_name", "last_name")
    @classmethod
    def _clean(cls, value: str) -> str:
        """Trimmed, and refused if the trim leaves nothing: « » passes `min_length` and
        would print as a blank in the report's masthead."""
        value = value.strip()
        if not value:
            raise ValueError("Name cannot be blank")
        return value


class AccountIn(NameIn):
    """Credentials for an account someone else is creating, plus who the account *is*.
    There is no self-service registration: the scope (which organization or unit) comes
    from the endpoint and the caller, never from the person being created.

    The name is required here even though the columns are nullable (migration 0007).
    That split is deliberate and is the same one the organization profile makes: the
    rows that predate the column have to be allowed to have no name, while nothing
    created *through the API* is allowed to be anonymous — a report has to be able to
    name the person who issued it.
    """
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)


class OrgAdminIn(AccountIn):
    organization_id: int


class UnitAdminIn(AccountIn):
    unit_id: int


class UserAccountIn(AccountIn):
    # A unit_admin creates users in their own unit and may leave this out; a
    # super_admin has to say which unit.
    unit_id: int | None = None


class MoveUnitIn(BaseModel):
    """Where to move an account that lives in a unit. Its role is unchanged by the move."""
    unit_id: int


class MoveOrganizationIn(BaseModel):
    """The same, for the one role that does not live in a unit: an organization's admin
    belongs to the organization directly, so its «ویرایش» is this and not `MoveUnitIn`."""
    organization_id: int


class PasswordResetIn(BaseModel):
    """An admin setting a password for someone below them. The old password is not
    required — the point is to restore access to an account whose password is lost."""
    password: str = Field(min_length=8, max_length=128)


class SelfPasswordIn(BaseModel):
    """`POST /auth/password` — an account changing its own password.

    Requiring the current one is the whole difference from `PasswordResetIn`, and it is
    what makes a self-service endpoint safe to have: an admin resetting somebody else's
    password is authorised by *being* that admin, while an account changing its own has
    only its token to show, and a token is exactly what gets left behind on a borrowed
    machine. Without this field an hour of borrowed access would be a permanent
    takeover.
    """
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    # Null only for an account created before the `first_name`/`last_name` columns
    # existed. `full_name` is computed rather than stored, so a client never has to
    # decide how to join the two — and `models.join_name` is what decides it, the same
    # function the PDF's masthead goes through.
    first_name: str | None = None
    last_name: str | None = None
    role: str
    is_active: bool = True
    # As stored: an org_admin carries organization_id, everyone below carries unit_id.
    organization_id: int | None = None
    unit_id: int | None = None

    @computed_field
    @property
    def full_name(self) -> str | None:
        return join_name(self.first_name, self.last_name)


class MeOut(UserOut):
    """`role` plus where the caller sits, resolved: a unit_admin or user reaches their
    organization through their unit, so `organization` is filled in for them here even
    though the column is NULL."""
    organization: OrganizationOut | None = None
    unit: UnitOut | None = None


class SearchIn(BaseModel):
    question: str = Field(min_length=1, max_length=500)


class JobFieldOut(BaseModel):
    """One dataset column of a matched record, ready to render as its own box.

    `items` is a list column split on its «|» separators and is empty for the two
    prose columns; `value` is display-ready either way (prose verbatim, a list joined
    with «،»). `primary` marks the columns the answer text was actually written from —
    what the question's intent asked for — so a client leads with those.

    `preview` is how many items to show before the reader asks for the rest. The whole
    column is always here: a record runs to 121 items at the median and 532 at the
    largest, which is more than anyone reads, but which of them matters is the reader's
    call and not ours — so the client slices at `preview` and its toggle costs no
    second request. The order the slice takes is not arbitrary either: `skills`,
    `knowledge`, `abilities` and `work_context` arrive in O*NET's own importance order,
    and `job_qa_service/columns.py:RANKED_FIELDS` are re-ordered against the question
    itself (see `engine._select_items`).
    """
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
    # The record(s) the answer came from, column by column. One entry for every mode
    # but out_of_domain; two for 'interdisciplinary'. The prose in `answer` stays the
    # answer — this is the underlying data, shown alongside it rather than instead of it.
    details: list[JobDetailOut] | None = None
    # job-request modes: nearest existing titles, and (when mode == 'job_generated')
    # the proposed record in JobIn's shape. The answer asks the user to confirm it;
    # on confirmation the client prefills its form from here and POSTs to
    # /jobs/suggestions. Kept as a plain mapping: a draft is model output, not a
    # validated record, and the user edits it before it is ever submitted.
    related_jobs: list[str] | None = None
    job_draft: dict[str, str] | None = None


# ---------- PDF report ----------
# The report is rendered from what the client already has on screen rather than from a
# fresh search: asking again would spend a second LLM call and could print prose the
# user never saw. That makes the payload untrusted input — it is the caller's own answer
# coming back, but nothing proves this server wrote it — so every string is bounded.
# The ceilings are far above any real answer and exist only to stop one account turning
# a download into an arbitrarily large render.
ReportText = Annotated[str, StringConstraints(max_length=20_000)]
ReportLabel = Annotated[str, StringConstraints(max_length=255)]


class ReportFieldIn(BaseModel):
    """One dataset column as `/search` handed it over. `key` is carried so the report
    can split sentence lists from label lists the way the client's boxes do; the rest
    is what JobFieldOut returned."""
    key: ReportLabel
    label: ReportLabel
    value: ReportText = ""
    items: list[ReportText] = Field(default=[], max_length=500)
    primary: bool = False


class ReportJobIn(BaseModel):
    job_title: ReportLabel
    fields: list[ReportFieldIn] = Field(default=[], max_length=40)


class ReportIn(BaseModel):
    """A `SearchOut` posted back for printing, plus the question that produced it.

    Deliberately not `SearchOut` itself: that type describes what this server *emits*,
    where every field is trusted and unbounded. This one describes what it will *accept*
    from a browser, which is a different thing even though the shapes match. Only the
    parts the report prints are here — `intent` and `scores` are not on the page.
    """
    question: str = Field(min_length=1, max_length=500)
    mode: ReportLabel
    answer: ReportText
    job: ReportLabel | None = None
    jobs: list[ReportLabel] | None = Field(default=None, max_length=8)
    details: list[ReportJobIn] = Field(default=[], max_length=4)
    related_jobs: list[ReportLabel] | None = Field(default=None, max_length=20)


# ---------- advanced search ----------
# The columns a person may describe themselves with, duplicated from
# `job_qa_service/columns.py:PROFILE_FIELDS` — the same duplication the ten content
# columns already live with, and for the same reason: `tests/conftest.py` replaces the
# whole engine package with a stub, so importing the real list here would break every
# test in the suite. The two lists must be changed together.
#
# `tools` is absent from both on purpose: 1099 of the 1116 cells hold untranslated
# English tool names, so a Persian item could never match one. The note in `columns.py`
# is the long version.
PROFILE_FIELDS = ["skills", "knowledge", "abilities", "responsibilities",
                  "work_context", "career_path_next"]
# Enough of a profile to be worth ranking 1116 records against. Fewer than this and the
# result is whatever the corpus happens to say most often, dressed up as an analysis.
PROFILE_REQUIRED_FIELD = "skills"
PROFILE_MIN_ITEMS = 2
PROFILE_MIN_FIELDS = 2
PROFILE_MAX_ITEMS = 20

# Bounded but not required to be non-empty: a blank arriving from the client's «+» row
# is dropped by the validator below rather than failing the whole request, which would
# refuse a profile over a box the user never filled in.
ProfileItem = Annotated[str, StringConstraints(max_length=120)]


class ProfileSearchIn(BaseModel):
    """`POST /search/advanced` — what someone can do, not a question about a job.

    Items arrive as a **list**, never as one «|»-joined string. That is the whole point
    of the client's repeatable input: the separator was a guess the user had to make
    («،» or «,» or «|»), and every wrong guess became one long item that matched
    nothing. Splitting is still done in the engine for pasted text, but the shape the
    API accepts is the shape the data has.
    """
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
    """One column of the profile, against one record: which of the user's items that
    record accounted for and which it did not — in the user's own words, so the list
    can be checked against what they typed."""
    key: str
    label: str
    matched: list[str] = []
    missing: list[str] = []
    ratio: float


class ProfileMatchOut(BaseModel):
    """One ranked record. `score` is what the order is by; `dense` and `coverage` are
    the two halves it was blended from, kept separate because they answer different
    questions — how alike the whole profile is, and how many of the listed items were
    literally found."""
    job_title: str
    score: float
    dense: float
    coverage: float
    fields: list[ProfileFieldOut] = []
    detail: JobDetailOut


class ProfileSearchOut(BaseModel):
    """Deliberately not `SearchOut`. That type is one answer about one job; this is a
    ranking, and it can never carry `job_draft` — the advanced path analyses the corpus
    that exists and never designs a record."""
    mode: str                       # 'profile_match' | 'out_of_domain'
    intent: str                     # always 'profile'
    answer: str
    job: str | None = None
    score: float | None = None
    matches: list[ProfileMatchOut] = []


class JobIn(BaseModel):
    """All dataset columns are required for a suggestion."""
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


# JobOut borrows JobIn's ten columns for their names and types, but must not inherit its
# *input* rules. Those say a suggestion has to fill every column in; the database says no
# such thing — a row can hold "" because it predates a column (knowledge/abilities before
# migration 0002), or because it was written by a script rather than through the API. With
# the constraints inherited, one such row made the whole listing fail response validation
# and `GET /admin/suggestions` answered 500.
#
# Stripped in a loop over JobIn's fields rather than by redeclaring each column here: a
# column added to JobIn later is covered automatically, instead of quietly bringing the
# 500 back with it.
for _column in JobIn.model_fields:
    JobOut.model_fields[_column].metadata = []
JobOut.model_rebuild(force=True)


class RebuildStatus(BaseModel):
    running: bool
    last_result: str | None


# ---------- dashboard statistics ----------
class SeriesPoint(BaseModel):
    """One calendar day and how many rows were created on it.

    Days rather than months: a Gregorian month is not a Persian one, and the client's
    axis is Persian. Bucketing here would force it to either re-split the buckets or
    mislabel them, so the grouping is left to the side that knows the calendar.
    """
    date: str                       # ISO 'YYYY-MM-DD'
    count: int


class RoleCount(BaseModel):
    role: str
    count: int


class UnitCount(BaseModel):
    unit_id: int
    name: str
    accounts: int


class JobStats(BaseModel):
    """The dataset and the caller's own share of it.

    `corpus_records` is global for everyone — it is the one shared dataset every
    organization searches, so its size is not a per-tenant number. `engine_records` is
    what the engine *currently serving searches* holds; the gap between the two is the
    approvals still waiting on a rebuild, and it is None while no engine is loaded.

    The three status counts are scoped: global for a super_admin, whose queue they are,
    and otherwise the records suggested by the accounts the caller manages.
    """
    corpus_records: int
    engine_records: int | None
    pending: int
    approved: int
    rejected: int


class StatsOut(BaseModel):
    """Aggregates for the dashboard, scoped exactly as `GET /accounts` is: everything
    for a super_admin, one organization for an org_admin, one unit for a unit_admin.

    Counts only — nothing here identifies an account, so it answers the same questions
    the account list would without handing over the list.
    """
    scope: str                      # 'global' | 'organization' | 'unit'
    scope_name: str | None
    organizations: int
    units: int
    accounts: int
    accounts_active: int
    accounts_blocked: int
    accounts_by_role: list[RoleCount]
    accounts_per_unit: list[UnitCount]
    jobs: JobStats
    # Creation dates, for the monthly bars. `organizations_series` is only ever
    # interesting to a super_admin; the others follow the caller's scope.
    accounts_series: list[SeriesPoint]
    units_series: list[SeriesPoint]
    organizations_series: list[SeriesPoint]
    suggestions_series: list[SeriesPoint]
