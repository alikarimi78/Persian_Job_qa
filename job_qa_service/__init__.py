# -*- coding: utf-8 -*-
"""
Persian Occupation Analysing engine (RAG) — production backend package.

Pipeline: hybrid dense retrieval (full-text + title/alias embeddings, BAAI/bge-m3)
fused with BM25 via Reciprocal Rank Fusion -> dual-gate out-of-domain check ->
single or interdisciplinary answer generated through an OpenAI-compatible API,
with automatic template fallback on any API failure.

A second entry path handles job *requests* ("شغلی می‌خواهم با این ویژگی‌ها..."):
the described spec is retrieved against the corpus and either an existing close
match is returned, or a brand-new occupation record is generated in the dataset's
own 10-column shape. A generated record is an *offer*, not a decision: the answer
names it and asks the user whether to register it, and the record itself is handed
back in `job_draft` so the client can prefill its suggestion form with it. Storing
it stays the user's call, and approving it stays the admin's.

A third path answers a *profile* rather than a sentence (`engine.analyze`): the user
lists what they can do, column by column, and the corpus is ranked against it by dense
similarity plus a per-item overlap that can name which of their items each job
accounted for. It is an analysis of the records that exist and never invents one —
that is what separates it from the job-request path above.

Backend usage:
    from job_qa_service import JobQAEngine
    engine = JobQAEngine("Merged_Occupations.xlsx")   # load once at startup
    result = engine.answer("وظایف افسر توپخانه چیست؟")  # thread-safe for reads
    ranked = engine.analyze({"skills": ["حل مسئله", "کار تیمی"], "tools": ["پایتون"]})

Modules, roughly in dependency order:
    config      env vars (read at import) + the thresholds retrieval is calibrated on
    columns     the ten canonical columns and the projections taken of them
    prompts     Persian system prompts;  messages  fixed text the user reads
    text        normalization, Markdown stripping, JSON extraction, corpus digest
    intents     is this a job request, and which columns answer this question
    bm25        sparse channel;          ranking   the question-path title tiebreak
    profile     advanced search: items in, coverage out (no embeddings of its own)
    llm         chat call that degrades to "" instead of raising
    render      context blocks, template answers, the per-field `details` payload
    emb_store   one cached vector per text, so a rebuild encodes only what changed
    engine      JobQAEngine: corpus, embeddings, retrieval, the three answer paths

Env vars (real process environment, not a .env): OPENAI_API_KEY, OPENAI_BASE_URL,
LLM_MODEL, EMBED_MODEL_NAME, EMB_CACHE_DIR, OCCUPATIONS_PATH
"""

import sys

# Force UTF-8 on stdio; malformed terminal bytes become � instead of crashing.
# At package import rather than in __main__, because it guards every Persian string
# this package prints — the REPL's answers and the server's log lines alike.
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
