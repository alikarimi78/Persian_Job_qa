from pydantic import BaseModel, Field, ConfigDict


class LoginIn(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str


# ---------- tenancy: organizations and units ----------
class OrganizationIn(BaseModel):
    name: str = Field(min_length=2, max_length=128)


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str


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


# ---------- accounts ----------
class AccountIn(BaseModel):
    """Credentials for an account someone else is creating. There is no self-service
    registration: the scope (which organization or unit) comes from the endpoint and
    the caller, never from the person being created."""
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


class PasswordResetIn(BaseModel):
    """An admin setting a password for someone below them. The old password is not
    required — the point is to restore access to an account whose password is lost."""
    password: str = Field(min_length=8, max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    role: str
    is_active: bool = True
    # As stored: an org_admin carries organization_id, everyone below carries unit_id.
    organization_id: int | None = None
    unit_id: int | None = None


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

    `items` is a list column split on its «|» separators and is empty for the three
    prose columns; `value` is display-ready either way (prose verbatim, a list joined
    with «،»). `primary` marks the columns the answer text was actually written from —
    what the question's intent asked for — so a client can open those and fold the rest.
    """
    key: str
    label: str
    value: str
    items: list[str] = []
    primary: bool = False


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
