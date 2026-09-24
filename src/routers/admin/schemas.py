from pydantic import BaseModel


class RebuildStatus(BaseModel):
    running: bool
    last_result: str | None
