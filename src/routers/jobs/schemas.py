from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# The ten content columns. Adding or renaming one here is only part of the change —
# see CLAUDE.md for the full list of places that have to move together.


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


# A stored record is read back as it is; the min_length bounds belong to the input side
# only, or a legacy row with an empty column could never be listed.
for _column in JobIn.model_fields:
    JobOut.model_fields[_column].metadata = []
JobOut.model_rebuild(force=True)


class JobPage(BaseModel):
    items: list[JobOut]
    total: int
    page: int
    page_size: int
