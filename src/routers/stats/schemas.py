from pydantic import BaseModel


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
