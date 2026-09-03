import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.database import connect, disconnect
from src.engine_manager import manager
from src.routers import accounts, admin, auth, jobs, orgs, reports, search, stats

log = logging.getLogger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    connect()
    try:
        manager.load()
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
