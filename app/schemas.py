from pydantic import BaseModel, Field, ConfigDict


class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str


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
    # Rows seeded before these columns existed hold ""; relaxing the inherited
    # min_length keeps them listable while new suggestions still have to supply them.
    knowledge: str = ""
    abilities: str = ""


class RebuildStatus(BaseModel):
    running: bool
    last_result: str | None
