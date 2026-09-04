from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

# The client posts back the `SearchOut` it already holds and nothing is stored, so this
# is untrusted input rather than a round-trip of our own model: every string is bounded
# and identity is never read from the body — the masthead comes from the token.
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
