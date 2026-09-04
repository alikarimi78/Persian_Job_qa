from pydantic import BaseModel

# The records themselves are `routers.jobs.schemas` — moderation reviews what users
# suggest, so the shapes are the same and only the rebuild status is admin's own.


class RebuildStatus(BaseModel):
    running: bool
    last_result: str | None
