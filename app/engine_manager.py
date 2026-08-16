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

_COLUMNS = ["job_title", "aliases", "tools", "skills", "knowledge", "abilities",
            "work_context", "career_path_next", "description", "responsibilities"]


def _approved_dataframe(db: Session) -> pd.DataFrame:
    rows = db.query(JobRecord).filter(JobRecord.status == JobStatus.approved).all()
    return pd.DataFrame([{c: getattr(r, c) or "" for c in _COLUMNS} for r in rows])


class EngineManager:
    def __init__(self):
        self._engine: JobQAEngine | None = None
        self._lock = threading.Lock()          # guards swaps and the two flags below
        self._rebuilding = False
        # A rebuild asked for while one was already running. Approving is what starts a
        # rebuild now (routers/admin.py), and a queue is moderated one click after
        # another: without this, the second approval would be refused a rebuild, the one
        # in flight would have read the database before that row was committed, and the
        # record would sit approved but unsearchable until somebody pressed the button.
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
        """How many records the engine answering searches right now holds — the corpus
        as of the last successful load, not as of the last approval. Against the
        approved rows in the database, the difference is what a rebuild would pick up.
        None while no engine is loaded, which is not the same as zero."""
        engine = self._engine
        return None if engine is None else len(engine.df)

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

    def rebuild_async(self, force_embeddings: bool = False) -> bool:
        """Returns False if a rebuild is already running — in which case one more pass is
        **queued** rather than dropped, and runs as soon as the current one finishes.

        Does *not* force a re-encode by default any more. The embedding store is keyed
        on the text of each record (job_qa_service/emb_store.py), so a rebuild after an
        approval encodes that record's two texts and reuses the other 2230; forcing
        would encode all of them, which is the ~31 min this used to cost on CPU.
        `force_embeddings` keeps that available for a store that has to be rewritten.

        The queued pass carries the *strongest* request it collected: a `force_embeddings`
        arriving during an ordinary rebuild is not silently downgraded to one."""
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
                except Exception as e:        # keep serving the old engine on failure
                    log.exception("Rebuild failed")
                    self.last_result = f"failed: {e}"
                # Clearing `_rebuilding` and reading `_rerun` has to be one step: a
                # request arriving between the two would find no rebuild running and
                # start none, having just been told one was.
                with self._lock:
                    if not self._rerun:
                        self._rebuilding = False
                        return
                    force, self._rerun, self._rerun_force = self._rerun_force, False, False

        threading.Thread(target=_work, daemon=True).start()
        return True


manager = EngineManager()
