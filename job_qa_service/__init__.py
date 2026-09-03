import sys

for _stream in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from .columns import (EXPECTED_COLUMNS, FIELD_LABELS, PROFILE_FIELDS, PROFILE_REQUIRED,
                      PROSE_COLUMNS)
from .engine import NOT_A_JOB, JobQAEngine
from .intents import detect_intent, is_job_request
from .render import build_context, job_detail
from .text import normalize_text

__all__ = [
    "JobQAEngine", "NOT_A_JOB",
    "normalize_text", "detect_intent", "is_job_request",
    "build_context", "job_detail",
    "EXPECTED_COLUMNS", "PROSE_COLUMNS", "FIELD_LABELS",
    "PROFILE_FIELDS", "PROFILE_REQUIRED",
]
