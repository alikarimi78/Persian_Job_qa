"""The application entry point: `uvicorn main:app`.

It sits at the repo root rather than inside `src/` so that the thing you run and the
thing you read are the same file — the package under it is the implementation. `src/` is
imported as a top-level package, which is why every command in the README is run from
here.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.database import connect, disconnect
from src.engine_manager import manager
from src.routers import accounts, admin, auth, jobs, orgs, reports, search, stats

log = logging.getLogger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The schema is Prisma's (`python -m scripts.prisma_cli migrate deploy`), not
    # something this process creates — the container runs it before uvicorn starts.
    #
    # The connection is opened here and not at import time on purpose: importing
    # `src.database` must stay cheap and side-effect-free, or the test suite and every
    # script would reach for a database merely by importing a router.
    connect()
    try:
        manager.load()                       # load QA engine from approved DB rows
    except RuntimeError as e:
        log.warning(f"Engine not loaded at startup: {e}")
    yield
    disconnect()


app = FastAPI(title="Persian Occupation Analysing API", version="1.0.0", lifespan=lifespan)

app.include_router(auth.router)
app.include_router(search.router)
app.include_router(reports.router)
app.include_router(jobs.router)
app.include_router(admin.router)
app.include_router(orgs.router)
app.include_router(accounts.router)
app.include_router(stats.router)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok", "engine_ready": manager._engine is not None,
            "rebuilding": manager.rebuilding}
