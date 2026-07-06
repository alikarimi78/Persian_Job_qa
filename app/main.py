import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .database import Base, engine as db_engine
from .engine_manager import manager
from .routers import auth, search, jobs, admin

log = logging.getLogger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=db_engine)
    try:
        manager.load()                       # load QA engine from approved DB rows
    except RuntimeError as e:
        log.warning(f"Engine not loaded at startup: {e}")
    yield


app = FastAPI(title="Persian Occupation Q&A API", version="1.0.0", lifespan=lifespan)

app.include_router(auth.router)
app.include_router(search.router)
app.include_router(jobs.router)
app.include_router(admin.router)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok", "engine_ready": manager._engine is not None,
            "rebuilding": manager.rebuilding}
