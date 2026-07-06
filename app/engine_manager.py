"""Owns the JobQAEngine singleton: loads it from Postgres at startup and
rebuilds it in the background on admin request (atomic swap, non-blocking)."""

import logging
import threading

import pandas as pd
from sqlalchemy.orm import Session

from job_qa_service import JobQAEngine
from .database import SessionLocal
from .models import JobRecord, JobStatus

log = logging.getLogger("engine_manager")

_COLUMNS = ["job_title", "aliases", "tools", "skills",
            "work_context", "career_path_next", "description", "responsibilities"]


def _approved_dataframe(db: Session) -> pd.DataFrame:
    rows = db.query(JobRecord).filter(JobRecord.status == JobStatus.approved).all()
    return pd.DataFrame([{c: getattr(r, c) or "" for c in _COLUMNS} for r in rows])


class EngineManager:
    def __init__(self):
        self._engine: JobQAEngine | None = None
        self._lock = threading.Lock()          # guards swaps
        self._rebuilding = False
        self.last_result: str | None = None

    @property
    def engine(self) -> JobQAEngine:
        if self._engine is None:
            raise RuntimeError("Engine not initialized")
        return self._engine

    @property
    def rebuilding(self) -> bool:
        return self._rebuilding

    def load(self, rebuild_embeddings: bool = False):
        db = SessionLocal()
        try:
            df = _approved_dataframe(db)
        finally:
            db.close()
        if df.empty:
            raise RuntimeError("No approved job records in the database; seed it first.")
        new_engine = JobQAEngine(df, rebuild_embeddings=rebuild_embeddings)
        with self._lock:
            self._engine = new_engine
        log.info(f"Engine loaded with {len(df)} approved records.")

    def rebuild_async(self) -> bool:
        """Returns False if a rebuild is already running."""
        if self._rebuilding:
            return False
        self._rebuilding = True

        def _work():
            try:
                self.load(rebuild_embeddings=True)
                self.last_result = "success"
            except Exception as e:            # keep serving the old engine on failure
                log.exception("Rebuild failed")
                self.last_result = f"failed: {e}"
            finally:
                self._rebuilding = False

        threading.Thread(target=_work, daemon=True).start()
        return True


manager = EngineManager()
