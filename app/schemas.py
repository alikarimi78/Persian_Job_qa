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


class SearchOut(BaseModel):
    mode: str
    intent: str
    answer: str
    job: str | None = None
    jobs: list[str] | None = None
    score: float | None = None
    scores: list[float] | None = None
    # job-request modes: nearest existing titles, and (when mode == 'job_generated')
    # the proposed record in JobIn's shape, ready to POST to /jobs/suggestions.
    # Kept as a plain mapping: a draft is model output, not a validated record.
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
