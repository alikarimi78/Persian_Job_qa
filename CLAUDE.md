# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A FastAPI backend that serves a Persian-language occupation Q&A service. `app/` is a thin
CRUD/auth/moderation layer over Postgres; the actual intelligence lives in the single module
`job_qa_service.py` (a self-contained RAG engine). All user-facing strings are Persian.

The dataset is O*NET data translated/summarized into Persian, flattened into **10 canonical columns**
that recur in every layer (DB model, Pydantic schemas, engine, seed script):

```
job_title, aliases, tools, skills, knowledge, abilities,
work_context, career_path_next, description, responsibilities
```

Seven are **list columns** — `aliases`, `tools`, `skills`, `knowledge`, `abilities`, `career_path_next`,
`responsibilities` — held as one string with `|` separators. The other three (`job_title`, `description`,
`work_context`) are **prose**, where a comma is punctuation. That split matters: an early
`data_extactor/aggregation_translated_jobs.py` pass ran the separator normalizer over *every* column and
replaced commas with `|` in 1060 prose cells ("ارزیابی | تایید ... | مسکن"). The script now normalizes
only the list columns, the dataset has been repaired, and `scripts/backfill_from_xlsx.py` fixes rows
already stored with the damage. Never introduce `|` into the three prose columns.

`Merged_Occupations.xlsx` also carries `row_index` and `source` (provenance: 1014 O*NET-derived rows +
102 generated military ones) — both are bookkeeping and are deliberately dropped at seed time, since
every reader projects onto its own column list rather than taking whatever the sheet holds.

Adding or renaming a content column means touching all of: `app/models.py`, `app/schemas.py`
(`JobIn` **and** `JobOut`), `app/engine_manager.py:_COLUMNS`, `scripts/seed_from_xlsx.py:COLUMNS`,
`job_qa_service.py`'s `EXPECTED_COLUMNS` / `FIELD_LABELS` / `_combined_text` / `DISCOVERY_FIELDS` /
`SYSTEM_JOB_GENERATE` (its JSON key list drives generated drafts), **the frontend's
`src/components/JobForm.jsx`** (see below), plus a migration. Missing any one of them fails quietly rather
than loudly — a column absent from `_combined_text` is simply never embedded, and one missing from
`JobForm` makes every suggestion submit fail with a 422 that names a field the form has no input for.

## Commands

Local dev uses the checked-out `venv/` (Python 3.10). Always run from the repo root — `job_qa_service.py`
is imported as a top-level module and `alembic.ini` relies on `prepend_sys_path = .`.

**Invoke tools as `venv/bin/python3 -m <tool>`, never `venv/bin/<tool>`.** The venv was created at a
different path, so every console script carries a stale shebang
(`#!/home/ali/Desktop/sarbazi/venv/bin/python`) and dies with `bad interpreter`. `venv/bin/python3` itself
is fine.

```bash
venv/bin/python3 -m uvicorn app.main:app --reload --port 8000   # dev server; Swagger at /docs
venv/bin/python3 -m alembic upgrade head                         # apply migrations
venv/bin/python3 -m alembic revision --autogenerate -m "describe change"  # after editing app/models.py
venv/bin/python3 -m scripts.seed_from_xlsx Merged_Occupations.xlsx  # seed corpus + admin user
venv/bin/python3 -m scripts.backfill_from_xlsx --dry-run          # preview a corpus sync
venv/bin/python3 -m py_compile app/*.py app/routers/*.py job_qa_service.py scripts/*.py  # syntax check
OCCUPATIONS_PATH=Merged_Occupations.xlsx venv/bin/python3 job_qa_service.py  # interactive engine REPL
```

`seed_from_xlsx` is a first-run tool: it skips the dataset entirely once `jobs_info` holds any row, so it
can never populate a column added later. `backfill_from_xlsx` is its counterpart for a live database — it
fills `knowledge`/`abilities` where empty (matched on `job_title`, whose key tolerates the `،`/`|` drift),
inserts dataset rows the database lacks as `approved`, repairs `|` left in the prose columns, leaves
pending suggestions untouched, and is idempotent. `--overwrite` also replaces
non-empty values; `--dry-run` reports and rolls back. Neither script refreshes the engine, so follow with
`POST /admin/rebuild`.

There is **no test suite, no pytest config, and no lint config** in this repo (Ruff is configured only
through the JetBrains plugin). Do not invent test commands. The engine REPL above is the fastest way to
exercise retrieval and answer generation without the API or a database; `eval_questions.csv`
(`question,expected` pairs) is a committed fixture with no runner attached to it yet.

### Docker

`Dockerfile` builds the API and its `CMD` chains `alembic upgrade head && seed_from_xlsx && uvicorn`.
**`docker-compose.yml` is deliberately not in this repo** — it was moved to a separate deployment repo
(commit `bddbc38`), so the `docker compose` commands in `README.md` only work from there. The compose
setup it expects: a `postgres:16` service named `db`, `env_file: .env`, and named volumes for
`/root/.cache/huggingface` and `/srv/emb_cache` (both caches are expensive to lose).

### Torch: local and container environments differ

The Dockerfile installs `torch==2.7.1` from the **PyTorch CPU index before** `requirements.txt`, so the
CPU wheel already satisfies `torch>=2.6,<2.8` and pip never pulls the CUDA build. Running a bare
`pip install -r requirements.txt` skips that step and drags in the CUDA wheel plus ~14 `nvidia-*`
packages — which is exactly what the checked-out `venv/` has (`2.7.1+cu126`). Keep the Dockerfile's
ordering when touching dependencies.

The consequence for behavior: `job_qa_service.py` picks its device from `_HAS_CUDA`, so encoding may run
on GPU locally and always runs on CPU in the container. Corpus re-encode timings measured locally are not
representative of production.

## Configuration — two independent paths

This is the most common source of confusion. Settings are read in two unrelated ways:

1. `app/config.py` — a pydantic-settings `Settings` object for the web layer.
2. `job_qa_service.py` — module-level `os.getenv(...)` constants (`OPENAI_API_KEY`, `OPENAI_BASE_URL`,
   `LLM_MODEL`, `EMBED_MODEL_NAME`, `EMB_CACHE_DIR`) evaluated **at import time**.

Because of (2), everything must be present in the **real process environment**. Loading a `.env` through
pydantic-settings would not reach the engine, and `Settings.Config.env_file = "../.env"` is a
CWD-relative path that resolves outside the project anyway — treat it as inert. In deployment the compose
`env_file` injects the variables into the environment, which is what makes it work.

`DATABASE_URL` may be set directly, or it is assembled in `app/config.py` from
`POSTGRES_USER`, `POSTGRES_PASSWORD`, `DATABASE_HOST`, `DATABASE_PORT`, `POSTGRES_DB` — **none of which
appear in `.env.example`**. The hardcoded `sqlalchemy.url` in `alembic.ini` is always overridden by
`alembic/env.py`, which pulls `settings.DATABASE_URL`.

## Architecture

### The corpus is the approved DB rows

There is one authoritative invariant: **the engine's corpus is exactly the `jobs_info` rows with
`status == approved`.** `app/engine_manager.py` materializes them into a pandas DataFrame and hands it to
`JobQAEngine`. Nothing reads the xlsx at request time — `Merged_Occupations.xlsx` is only a seed input.

`EngineManager` (module-level singleton `manager`) owns the engine:

- `main.py` lifespan calls `manager.load()` at startup; if the table is empty it logs a warning and the
  app still boots. `/search` then returns 503 until an engine exists, and `/health` reports `engine_ready`.
- `POST /admin/rebuild` calls `rebuild_async()`, which builds a **whole new engine on a daemon thread**
  and atomically swaps it in under a lock. Reads keep serving the old engine throughout, and a failed
  rebuild leaves the old one in place with the error recorded in `manager.last_result`.
- Approving a suggestion does **not** refresh the engine. A new record only becomes searchable after an
  explicit rebuild (or a restart).
- `/search` wraps `engine.answer` in `run_in_threadpool` — encoding and the LLM call are blocking.

### Moderation flow

Users register (`app/routers/auth.py`, JWT bearer via PyJWT + bcrypt) and submit complete records to
`POST /jobs/suggestions`, which land as `pending`. Admins list and approve/reject them under `/admin/*`
(the whole router is gated by `dependencies=[Depends(require_admin)]`; `_review` rejects any record that
is not still `pending`). `POST /admin/jobs` inserts as `approved` directly. The admin account is created
from `ADMIN_USERNAME`/`ADMIN_PASSWORD` by the seed script, not by a migration.

### The client is a sibling repo

`../job_qa_frontend` (React + Vite + react-router, its own git repo; `vite.config.js` proxies `/api` to
`localhost:8000` in dev and nginx does it in production). Two things there are coupled to this repo:

- `src/components/JobForm.jsx` holds the ten columns a second time, and backs both `POST /jobs/suggestions`
  and `POST /admin/jobs`. It is the one place outside this repo that a column change has to reach.
- The discovery confirmation spans the two services. `Search.jsx` sees `mode: job_generated`, shows the
  offer with accept/decline buttons, and on accept stashes `job_draft` through `src/draft.js` before
  routing to `/suggest`, which prefills the editable form from it. The stash is sessionStorage rather than
  router state because `/search` is public while `/jobs/suggestions` is not: an anonymous user is detoured
  through `/login` (`Protected` passes the attempted location, login returns them to it), and the accepted
  draft has to survive that hop.

### The engine (`job_qa_service.py`)

`answer()` branches on intent detection **before** retrieval:

- **Job-request path** — `is_job_request()` decides this in two layers: a literal `JOB_REQUEST_KEYWORDS`
  list of fixed idioms (both ZWNJ and plain-space spellings, since hazm leaves «میخوام» alone), plus
  `_JOB_REQUEST_RE`, which generalizes them. The pattern layer keys on **indefiniteness** — «شغلی»,
  «یه کاری» — because that is what separates wanting *a* job from asking about *the* job you named;
  «یه کاری که توش ... کار کنم» is a request, «محیط کاری راننده زره‌پوش» is not. Two exclusions are load-bearing
  and were both regressions caught in testing: «مناسب» is kept out of the desire verbs (it would swallow
  «محیط کاری مناسب برای پرستار چیست؟») and «کار» is kept out of the «چه ...» interrogative (in «چه کاری
  انجام می‌دهد؟» it means *task*, not *occupation*). Widening either one silently routes duties questions
  into record generation. Routes to
  `_discover()`, which either returns an existing close match (`mode: job_match`, dense ≥ `DISCOVERY_MATCH`),
  or asks the LLM to design a brand-new record and returns it as `job_draft` (`mode: job_generated`) — a
  dict already shaped like `JobIn`. Below `DISCOVERY_FLOOR` on both channels it refuses to invent anything.

  A generated record is an **offer, not a decision**. `_render_draft` deliberately puts only the proposed
  title and one-line description in `answer` and ends by asking the user whether to register it; the full
  record rides along in `job_draft` for the client to prefill its suggestion form with. Nothing is stored
  until the user submits that form (→ `pending`) and an admin approves it. Keep those two apart when
  editing: `answer` is the question, `job_draft` is the payload — don't move fields between them and don't
  make the client parse the answer text back into a record. `_generate_job` also rewrites `|` to «،» in
  `PROSE_COLUMNS`, because a draft flows straight into `jobs_info` and a model reaching for the list
  separator in prose would reintroduce exactly the corruption the dataset was repaired of.
- **Question path** — `detect_intent()` maps Persian keywords to which columns to feed the LLM
  (`INTENT_TO_FIELDS`); a short input with no question word is treated as a `description` request.
  Returns `mode: single` or `interdisciplinary` (two distinct top jobs with near-equal scores, or an
  explicit «بین‌رشته‌ای»-style request).

Retrieval (`_retrieve`) is hybrid: a weighted dense score over two embedding matrices (full record text
`W_FULL` + title/alias text `W_TITLE`, `BAAI/bge-m3`, normalized so dot product is cosine) fused with a
hand-rolled `BM25` class via Reciprocal Rank Fusion. BM25 scores are divided by the maximum a
full-match document could reach, which is what keeps the sparse out-of-domain gate meaningful.
Out-of-domain requires **both** channels to be weak.

Two properties to preserve when editing:

- **Every LLM call degrades gracefully.** `_llm()` retries with exponential backoff and returns `""` on
  any failure (or when no API key is set); each caller falls back to a plain-text template
  (`_template_one` / `_template_two` / nearest-job list) so the endpoint never fails because the API did.
- **Answers must be plain text.** The Persian system prompts forbid Markdown and `_clean_markdown()`
  strips it. JSON-returning calls pass `clean=False`, since stripping would corrupt them.

The threshold constants near the top of the file (`THRESHOLD_MATCH`, `THRESHOLD_SPARSE`, `SECONDARY_MIN`,
`SECONDARY_MARGIN`, `PAIR_SIM_MAX`, `DISCOVERY_MATCH`, `DISCOVERY_FLOOR`) are calibrated for bge-m3 on
this dataset and documented inline with the score ranges they assume. Changing the embedding model
invalidates all of them.

### Embedding cache

`_load_or_build_embeddings` caches to `emb_cache/corpus_{model}_{row_count}_{fingerprint}.npz`, where the
fingerprint is a sha256 digest (`_corpus_fingerprint`) over the embedding model name and every text that
gets encoded. Keying on content rather than row count is deliberate: it means editing a record, or
changing how `_combined_text` assembles a row, misses the cache automatically instead of serving vectors
built from text that no longer exists. Nothing has to be invalidated by hand.

- A miss re-encodes the whole corpus (~1100 records). Fast on GPU, slow on the container's CPU-only torch.
- Superseded cache files are never deleted — `emb_cache/` accumulates one `.npz` per distinct corpus, so
  prune it occasionally (each is ~9 MB).
- `POST /admin/rebuild` passes `rebuild_embeddings=True`, which bypasses the cache read entirely.
- The Dockerfile does not copy `emb_cache/`; containers rely on the mounted volume.

## `data_extactor/` — offline data pipeline

One-off scripts, not part of the runtime. `aggrigation_script_for_jobs.py` aggregates the raw O*NET
xlsx files in `dataset_jobs/` into `onet_master_database_en.xlsx`; `aggregation_translated_jobs.py`
merges the per-batch Persian translations in `translated_xlsx/` and normalizes separators to `|`.
Note it writes to `../scripts/Merged_Occupations.xlsx` while the seed script and Dockerfile both expect
`Merged_Occupations.xlsx` at the repo root — the file has to be moved after regeneration. These scripts
use relative paths and must be run from inside `data_extactor/`. Their column set (`industry`, `level`,
`hard_skills`/`soft_skills`, …) is wider than the 8 columns the backend uses.
