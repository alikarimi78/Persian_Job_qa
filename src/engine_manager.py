import logging
import threading

import pandas as pd
from prisma import Prisma

from job_qa_service import JobQAEngine
from .database import db as _db
from .models import JobStatus

log = logging.getLogger("engine_manager")

_COLUMNS = ["job_title", "aliases", "tools", "skills", "knowledge", "abilities",
            "work_context", "career_path_next", "description", "responsibilities"]


def _approved_dataframe(db: Prisma) -> pd.DataFrame:
    rows = db.jobrecord.find_many(where={"status": JobStatus.approved})
    return pd.DataFrame([{c: getattr(r, c) or "" for c in _COLUMNS} for r in rows])


class EngineManager:
    def __init__(self):
        self._engine: JobQAEngine | None = None
        self._lock = threading.Lock()
        self._rebuilding = False
        self._rerun = False
        self._rerun_force = False
        self.last_result: str | None = None

    @property
    def engine(self) -> JobQAEngine:
        if self._engine is None:
            raise RuntimeError("Engine not initialized")
        return self._engine

    @property
    def rebuilding(self) -> bool:
        return self._rebuilding

    @property
    def record_count(self) -> int | None:
        engine = self._engine
        return None if engine is None else len(engine.df)

    def load(self, rebuild_embeddings: bool = False):
        df = _approved_dataframe(_db)
        if df.empty:
            raise RuntimeError("No approved job records in the database; seed it first.")
        new_engine = JobQAEngine(df, rebuild_embeddings=rebuild_embeddings)
        with self._lock:
            self._engine = new_engine
        log.info(f"Engine loaded with {len(df)} approved records.")

    def rebuild_async(self, force_embeddings: bool = False) -> bool:
        with self._lock:
            if self._rebuilding:
                self._rerun = True
                self._rerun_force = self._rerun_force or force_embeddings
                return False
            self._rebuilding = True

        def _work():
            force = force_embeddings
            while True:
                try:
                    self.load(rebuild_embeddings=force)
                    self.last_result = "success"
                except Exception as e:
                    log.exception("Rebuild failed")
                    self.last_result = f"failed: {e}"
                with self._lock:
                    if not self._rerun:
                        self._rebuilding = False
                        return
                    force, self._rerun, self._rerun_force = self._rerun_force, False, False

        threading.Thread(target=_work, daemon=True).start()
        return True


manager = EngineManager()
