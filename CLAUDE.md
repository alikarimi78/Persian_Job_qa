# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A FastAPI backend that serves a Persian-language occupation Q&A service. `app/` is a thin
CRUD/auth/moderation layer over Postgres, with an organization → unit → user hierarchy on top of it
(see *Roles and tenancy*); the actual intelligence lives in the `job_qa_service/` package (a
self-contained RAG engine, no dependency on `app/` in either direction). All user-facing strings are
Persian.

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
`job_qa_service/columns.py`'s `EXPECTED_COLUMNS` / `FIELD_LABELS` / `DISCOVERY_FIELDS`
(which also feeds `DETAIL_FIELDS`, so the search result's per-field boxes follow along),
`job_qa_service/engine.py:_combined_text`, `job_qa_service/prompts.py`'s
`SYSTEM_JOB_GENERATE` (its JSON key list drives generated drafts), **the frontend's
`src/components/JobForm.jsx`** (see below), plus a migration. Missing any one of them fails quietly rather
than loudly — a column absent from `_combined_text` is simply never embedded, and one missing from
`JobForm` makes every suggestion submit fail with a 422 that names a field the form has no input for.
A column that is prose rather than a `|`-joined list also belongs in `PROSE_COLUMNS`; otherwise its box
renders the sentence as a one-item list.

## Commands

Local dev uses the checked-out `venv/` (Python 3.10). Always run from the repo root — `job_qa_service/`
is imported as a top-level package and `alembic.ini` relies on `prepend_sys_path = .`.

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
venv/bin/python3 -m py_compile app/*.py app/routers/*.py job_qa_service/*.py scripts/*.py  # syntax check
OCCUPATIONS_PATH=Merged_Occupations.xlsx venv/bin/python3 -m job_qa_service  # interactive engine REPL
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

The consequence for behavior: `job_qa_service/engine.py` picks its device from `_HAS_CUDA`, so encoding
may run on GPU locally and always runs on CPU in the container. Corpus re-encode timings measured
locally are not representative of production.

## Configuration — two independent paths

This is the most common source of confusion. Settings are read in two unrelated ways:

1. `app/config.py` — a pydantic-settings `Settings` object for the web layer.
2. `job_qa_service/config.py` — module-level `os.getenv(...)` constants (`OPENAI_API_KEY`,
   `OPENAI_BASE_URL`, `LLM_MODEL`, `EMBED_MODEL_NAME`, `EMB_CACHE_DIR`) evaluated **at import time**.
   The two files have deliberately similar names and nothing to do with each other.

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

### Roles and tenancy

Two levels of tenancy — a **user** sits in a **unit**, a unit sits in an **organization** — and four
roles, each provisioned by the one above it. **There is no self-registration**: `POST /auth/register` is
gone, so every account exists because someone with authority created it, and every endpoint but
`/health` requires a token (including `/search`, which was public before this).

| role | scope column | creates |
|---|---|---|
| `super_admin` | none (sees everything) | organizations, their admins, further super admins |
| `org_admin` | `users.organization_id` | the units of their own organization, and each unit's admin |
| `unit_admin` | `users.unit_id` | the ordinary users of their own unit |
| `user` | `users.unit_id` | nobody — searches and suggests jobs |

```
POST /accounts/super-admins   super_admin
POST /accounts/org-admins     super_admin                      one per organization
POST /accounts/unit-admins    super_admin | org_admin          one per unit
POST /accounts/users          super_admin | unit_admin
GET  /accounts                super_admin | org_admin | unit_admin   scoped, ?role=&unit_id=&organization_id=
POST   /accounts/{id}/block · /unblock · /password     any admin, over accounts below them
POST   /accounts/{id}/unit    super_admin | org_admin  move an account to another unit
DELETE /accounts/{id}         any admin, over accounts below them
POST /orgs · GET /orgs · GET /orgs/{id} · DELETE /orgs/{id}     super_admin (org_admin reads its own)
POST /units · GET /units · GET /units/{id} · DELETE /units/{id} super_admin | org_admin
                                                 (a unit_admin only *reads* its own unit)
GET  /auth/me                 any account: role plus the organization/unit it sits in
```

An org_admin deliberately **cannot** create ordinary users — it creates the units and their admins, and
those admins staff their own unit. A super_admin may stand in at any level, but must then name the target
(`organization_id`/`unit_id`) explicitly, since it has no scope of its own to default to.

Three invariants are enforced by the database, not only by the handlers (`app/models.py:User.__table_args__`,
mirrored in migration `0003`):

- **One admin per organization and per unit** — partial unique indexes (`uq_users_org_admin`,
  `uq_users_unit_admin`) over the scope column `WHERE role = '…_admin'`, so ordinary users still share a
  unit freely. `accounts.create_account` checks first only to return a 409 that names the sitting admin.
- **A role carries exactly its own scope column** — `ck_users_scope`. A unit_admin's organization is
  reached through its unit and is never stored twice, so the two cannot drift; `User.scope_organization_id`
  and `GET /auth/me` resolve it. The one deliberate hole: `role='user'` may have a NULL `unit_id`, because
  rows predating this hierarchy have no unit to guess. New accounts always carry one.
- **Usernames are global**, not per-tenant, because login is by username alone.

`app/auth.py:require_roles(*roles)` gates *who* may call an endpoint; *which* organization or unit they
may touch is a second check in the handler, against the target record — `accounts.assert_manages_organization`
/ `assert_manages_unit`. Both are needed: an org_admin passes the role gate for `POST /units` and is then
refused a unit in someone else's organization. The role is re-read from the database on every request
rather than trusted from the JWT, so a token minted before a change carries no stale rights.

**Blocking, password reset, moving and deletion** all answer the same question as creation — may the
caller act on this account — through `accounts.assert_can_manage_account`, the provisioning chain read
downwards:
you may act on the accounts you could have created. An org_admin therefore reaches the unit admins and
users of its own organization but not its peers; a unit_admin reaches only the ordinary users of its unit.
**Nobody may act on their own account**, at any level: an admin who blocks themselves would need someone
above them to undo it, and a unit_admin has nobody above them inside their unit.

`is_active` is checked in two places on purpose. `routers/auth.py:login` refuses a blocked account with
403 rather than the 401 given for a wrong password — the password *was* right, and the person needs to
know to ask their admin instead of retrying. `auth.py:get_current_user_optional` checks it again on every
request, so a token minted before the block dies with it instead of lasting out its hour. Blocking is not
inherited: blocking a unit_admin leaves that unit's users able to log in. A blocked account keeps its row,
its unit and everything it has suggested; nothing is deleted.

**Moving** (`POST /accounts/{id}/unit`) needs two permissions at once, and that pair is what keeps an
org_admin inside their organization: `assert_can_manage_account` over the account plus `assert_manages_unit`
over the destination. It is closed to a unit_admin on purpose — they run one unit, and a move is a decision
about two of them. Only accounts that live in a unit can move (`user`, `unit_admin`; an org_admin belongs to
an organization and a super_admin to nothing), the role is unchanged by the move, and a unit_admin may only
land in a unit that has no admin — checked in `move_to_unit` so the answer is a 409 naming the sitting admin
rather than an IntegrityError from the partial unique index.

**Deletion** (`DELETE /accounts/{id}`) is the irreversible counterpart of blocking. It depends on migration
`0005`: `jobs_info.suggested_by` / `reviewed_by` are `ON DELETE SET NULL`, so a record the deleted account
suggested stays in the corpus and only loses its attribution. `SET NULL` and not `CASCADE` — an approved
record is part of the dataset everyone searches and must not vanish because its author left. Without that
rule the delete would simply fail on the foreign key for anyone who had ever suggested anything.

Nothing here can strand the system without a super_admin: every one of these actions goes strictly
downwards and nobody may act on their own account, so whoever performs the last such change is still
standing afterwards.

**Deleting a unit or an organization requires it to be empty** — a unit of accounts (its admin included),
an organization of both units and accounts — and answers 409 naming what is still in the way. There is no
cascade on purpose: emptying it is the same work either way, and this way every account is deleted or
moved by someone who looked at it, instead of one click taking out an organization's people. It also means
the existing foreign keys are never violated, so deletion needed no migration of its own. Deleting a unit
is closed to a unit_admin for the same reason creating one is: they staff the unit, they do not decide
whether it exists.

Not implemented, and absent on purpose rather than forgotten: renaming an organization or unit, and a user
changing their own password (only an admin above them can, via `/accounts/{id}/password`, which
deliberately does not ask for the old one — it exists for the account that cannot supply it).

### Moderation flow

Users submit complete records to `POST /jobs/suggestions`, which land as `pending`. They are reviewed
under `/admin/*` (the whole router is gated by `dependencies=[Depends(require_super_admin)]`; `_review`
rejects any record that is not still `pending`). `POST /admin/jobs` inserts as `approved` directly.

Moderation is **super-admin-only**, including for org and unit admins: approving a suggestion writes into
the one global corpus every organization searches, so it is not an organization-level decision. The
tenancy scopes accounts, not job records — there is a single shared dataset.

The first super admin is created from `ADMIN_USERNAME`/`ADMIN_PASSWORD` by the seed script, not by a
migration; every other account comes from the API. Migration `0003` maps the old single `admin` role onto
`super_admin`, so an existing deployment's admin keeps working.

### The client is a sibling repo

`../job_qa_frontend` (React + Vite + react-router, its own git repo; `vite.config.js` proxies `/api` to
`localhost:8000` in dev and nginx does it in production). Two things there are coupled to this repo:

- `src/components/JobForm.jsx` holds the ten columns a second time, and backs both `POST /jobs/suggestions`
  and `POST /admin/jobs`. It is the one place outside this repo that a column change has to reach.
- `src/components/JobDetails.jsx` renders the `details` payload of a search result as one collapsible box
  per field, under the generated answer. It is deliberately *not* a third copy of the column list: labels,
  order, list-splitting and which boxes open all arrive from the backend, so a new content column shows up
  in the boxes on its own. The only thing it decides locally is shape — duties render as a bulleted list
  because they are sentences, other list columns as chips, prose as a paragraph.
- The discovery confirmation spans the two services. `Search.jsx` sees `mode: job_generated`, shows the
  offer with accept/decline buttons, and on accept stashes `job_draft` through `src/draft.js` before
  routing to `/suggest`, which prefills the editable form from it. The stash is sessionStorage rather than
  router state so the accepted draft survives a redirect through `/login`. That detour is now rare — every
  page including `/` is behind `Protected` since `/search` started requiring a token — but the stash still
  earns its keep across an expired session.

- `src/pages/Manage.jsx` (`/manage`) is the provisioning chain as one page: which sections render depends
  on the caller's role, mirroring the API's permissions — organizations and super admins for a
  super_admin, units for an org_admin, users for a unit_admin, and the account table for everyone who has
  one. It reads `GET /auth/me` for the caller's own scope, and tolerates a 403 from `/orgs` because a
  unit_admin is not allowed to list organizations.
  `src/components/AccountsTable.jsx` carries the row actions — block/unblock, reset password, move to a
  unit, delete (behind an inline confirmation, since it is the one irreversible action). It repeats the
  backend's rules in `canManage()` and `canMove()` purely so the table does not offer a button that would
  come back 403; the server is still the one enforcing them, and the pairs must be changed together.
  Organizations and units get the same inline confirmation, and the page does not try to predict whether
  one is empty — it asks, and shows the 409 the server answers with.

Self-registration is gone from the client too (the page was deleted, `/` sits behind `Protected` now that
`/search` needs a token, and the admin links test for `super_admin`). The styling of `/manage` is
deliberately plain — it reuses the existing card/badge/table classes and is expected to be restyled.

### The engine (`job_qa_service/`)

One package, imported as a top-level module (`from job_qa_service import JobQAEngine`). It was a single
800-line file until it was split along its existing seams; the split moved code and renamed the helpers
that crossed a module boundary, and changed no behavior — the corpus fingerprint is unchanged, so the
embedding cache still hits.

| module | holds |
|---|---|
| `config.py` | env vars read at import + every calibrated threshold |
| `columns.py` | the ten columns and the projections of them (`DISCOVERY_FIELDS`, `DETAIL_FIELDS`, …) |
| `prompts.py` / `messages.py` | Persian system prompts / fixed text the user reads |
| `text.py` | `normalize_text`, `clean_markdown`, `parse_json_object`, `corpus_fingerprint` |
| `intents.py` | `is_job_request`, `detect_intent`, and their keyword/pattern tables |
| `bm25.py` / `ranking.py` | sparse channel / the question-path title tiebreak |
| `llm.py` | `LLMClient`: the chat call that returns `""` instead of raising |
| `render.py` | `build_context`, `template_one`/`template_two`, `render_draft`, `job_detail` |
| `engine.py` | `JobQAEngine`: corpus, embeddings, retrieval, the two answer paths |

`__init__.py` re-exports the public names and is where stdio is forced to UTF-8; `__main__.py` is the
REPL, so the entrypoint is `python3 -m job_qa_service`.

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

  **`DISCOVERY_FLOOR` is a cheap pre-filter, not the realism check.** Retrieval cannot tell a real
  occupation the corpus lacks from a fictional one, because dense similarity is topical: «تربیت اژدها»
  measures 0.466 against «مربیان حیوانات» while the real-but-niche «عصاره‌گیری گیاهان دارویی» measures
  0.515, and across a probe set the two ranges overlap outright (real 0.479–0.693, fictional 0.416–0.526).
  No threshold separates them — raising the floor only starts dropping legitimate requests. So the
  judgment lives where the world knowledge is: `SYSTEM_JOB_GENERATE` rule 5 tells the model to return
  `{"not_a_job": true}` for anything fictional, magical, or physically impossible, `_generate_job` turns
  that into the `NOT_A_JOB` sentinel, and `_discover` answers with `DISCOVERY_NOT_REAL` plus the nearest
  real jobs. That rule previously said the opposite — "design the nearest real equivalent" — which is how
  a dragon-training request produced a full «پرورش‌دهنده اژدها» record headed for the moderation queue.
  The sentinel is deliberately distinct from `None`: `None` means the API failed and still answers with
  `DISCOVERY_UNAVAILABLE`, so an outage is never reported to the user as "that isn't a real job".

  A generated record is an **offer, not a decision**. `render.render_draft` deliberately puts only the
  proposed title and one-line description in `answer` and ends by asking the user whether to register
  it; the full record rides along in `job_draft` for the client to prefill its suggestion form with.
  Nothing is stored until the user submits that form (→ `pending`) and an admin approves it. Keep those two apart when
  editing: `answer` is the question, `job_draft` is the payload — don't move fields between them and don't
  make the client parse the answer text back into a record. `_generate_job` also rewrites `|` to «،» in
  `PROSE_COLUMNS`, because a draft flows straight into `jobs_info` and a model reaching for the list
  separator in prose would reintroduce exactly the corruption the dataset was repaired of.
- **Question path** — after retrieval, `ranking.prefer_title_match()` may promote a runner-up whose
  *title* shares content words with the question. Dense similarity put «خدمه توپخانه و موشک» (0.667)
  above «افسران توپخانه و موشک» (0.650) for «وظایف افسر توپخانه چیست؟» — right unit, wrong rank — and the
  answer then described the crew to someone asking about officers, or refused outright with «اطلاعات
  کافی...» in 5 runs out of 6 because `SYSTEM_SINGLE` rule 6 read crew-vs-officer as unrelated. The
  refusal was the symptom; the ranking was the bug. Matching is prefix-based in both directions so
  «افسر» reaches «افسران», and the tiebreak is deliberately timid: it only considers candidates within
  `TITLE_TIEBREAK_MARGIN` of the leader and requires a *strict* overlap improvement, so an unbeaten
  leader or a no-overlap tie leaves the dense order alone. It is called only here, never in `_discover()`,
  where a description of duties is matched against titles and the overlap would be noise. Rule 6 itself
  was left alone: probed against six related-but-not-exact questions it refused none of them, so there is
  no failing case to justify editing the prompt.
- **Question path** — `detect_intent()` maps Persian keywords to which columns to feed the LLM
  (`INTENT_TO_FIELDS`); a short input with no question word is treated as a `description` request.
  Returns `mode: single` or `interdisciplinary` (two distinct top jobs with near-equal scores, or an
  explicit «بین‌رشته‌ای»-style request).

Every mode except `out_of_domain` also returns **`details`**: the matched record(s) column by column,
built by `render.job_detail()`. This is the same data the prose was written from, handed over
structured so the client can show it as one box per field. It does not replace `answer` and does not
change it — the generated sentences stay exactly as they were, and the boxes sit under them.

- `primary` flags the columns the answer actually used — `INTENT_TO_FIELDS[intent]` on the question path,
  `DISCOVERY_PRIMARY` on the discovery path, which has no intent to key on because the user described a
  job instead of asking about one facet of it. Those boxes are meant to be shown open, the rest folded,
  and `job_detail` sorts them first so a client that ignores the flag still leads with the right field.
- `items` splits a list column on its `|`; the three `PROSE_COLUMNS` get `[]`, since there a comma is
  punctuation and the cell is one piece of text. `value` is display-ready either way — prose verbatim, a
  list re-joined with «،» — so a client that never looks at `items` still cannot render a raw `|`.
- Cells holding the dataset's «nothing here» placeholder (`EMPTY_CELLS`, mostly a bare `-`) are dropped
  rather than rendered as an empty box. `DETAIL_FIELDS` is derived from `DISCOVERY_FIELDS`, so a new
  content column reaches the boxes through that one list.
- `mode: job_generated` gets `details` too, describing the *proposed* record so the user can read what
  they would be registering before accepting. That does not blur the `answer`/`job_draft` split above:
  `job_draft` is still the only thing posted to `/jobs/suggestions`, and `details` is still only shown.

Retrieval (`engine._retrieve`) is hybrid: a weighted dense score over two embedding matrices (full
record text `W_FULL` + title/alias text `W_TITLE`, `BAAI/bge-m3`, normalized so dot product is cosine)
fused with the hand-rolled `BM25` class in `bm25.py` via Reciprocal Rank Fusion. BM25 scores are divided
by the maximum a full-match document could reach, which is what keeps the sparse out-of-domain gate
meaningful.
Out-of-domain requires **both** channels to be weak.

Two properties to preserve when editing:

- **Every LLM call degrades gracefully.** `llm.LLMClient` retries with exponential backoff and returns
  `""` on any failure (or when no API key is set); each caller falls back to a plain-text template
  (`render.template_one` / `template_two` / nearest-job list) so the endpoint never fails because the
  API did.
- **Answers must be plain text.** The Persian system prompts forbid Markdown and `text.clean_markdown()`
  strips it. JSON-returning calls pass `clean=False`, since stripping would corrupt them.

The threshold constants in `config.py` (`THRESHOLD_MATCH`, `THRESHOLD_SPARSE`, `SECONDARY_MIN`,
`SECONDARY_MARGIN`, `PAIR_SIM_MAX`, `DISCOVERY_MATCH`, `DISCOVERY_FLOOR`) are calibrated for bge-m3 on
this dataset and documented inline with the score ranges they assume. Changing the embedding model
invalidates all of them.

### Embedding cache

`engine._load_or_build_embeddings` caches to `emb_cache/corpus_{model}_{row_count}_{fingerprint}.npz`,
where the fingerprint is a sha256 digest (`text.corpus_fingerprint`) over the embedding model name and
every text that gets encoded. Keying on content rather than row count is deliberate: it means editing a
record, or changing how `_combined_text` assembles a row, misses the cache automatically instead of
serving vectors built from text that no longer exists. Nothing has to be invalidated by hand.

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
