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
renders the sentence as a one-item list. If people should be able to *search* on it, it goes in
`PROFILE_FIELDS` as well — which is three lists, not one: `job_qa_service/columns.py`, its copy in
`app/schemas.py`, and `src/components/search/AdvancedSearch.jsx`'s `FIELDS`. The copy in `app/schemas.py` exists because
`tests/conftest.py` stubs the whole engine package away, so importing the real list there would break
the suite; a field named in one and not the others is a 422 naming a key the form has no box for.

A **list column is entered one item at a time** in the client (`ui/ItemsInput`) and joined with `|` at
the edge, so nothing downstream changed — but a new list column has to be in `JobForm`'s `LISTS` rather
than its `PROSE`, or it is submitted as a bare string and the items are never split.

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

```bash
venv/bin/python3 -m pip install -r requirements-dev.txt   # pytest + httpx, once
venv/bin/python3 -m pytest                                # the whole suite, ~20 s
```

`tests/` covers **`app/` only** — the scope and permission rules (`test_account_scope.py`,
`test_accounts_api.py`, `test_tenancy_api.py`), what a token proves (`test_auth.py`), the settings that
must crash rather than default (`test_config.py`), the two rate limits (`test_rate_limit.py`), that
the dashboard's counts stop where its caller's authority does (`test_stats_api.py`), what the PDF
report puts on a page (`test_reports_api.py`), and what a profile has to carry before advanced search
will run one (`test_advanced_search_api.py` — the contract and the gates around the ranking, never the
ranking itself, which needs the real engine), and who may correct a suggestion and while it is in what
state (`test_admin_suggestions_api.py`). It
needs neither Postgres nor torch: `tests/conftest.py` puts the required variables in `os.environ`,
points `DATABASE_URL` at in-memory SQLite, and installs a **stub `job_qa_service`** in `sys.modules`
before `app` is imported, because `app.routers.search` pulls the engine package in through
`app.engine_manager` and the real one costs 20 s of torch import per run. Anything that has to exercise
retrieval belongs in the REPL, not here.

The report tests are the one exception to "no heavy dependency": WeasyPrint is imported for real, so
the suite needs libpango present the same way the container does. They assert mostly on `build_html`
rather than on a render, because a PDF costs ~0.5 s and by that point the text is already decided —
only one test renders, and it renders every answer mode, since a template error shows up nowhere else.

Two consequences worth knowing before editing either side. The SQLite the tests run on only reproduces
"one admin per organization/unit" because `app/models.py` carries a `sqlite_where` twin of each
`postgresql_where`; drop it and a second ordinary user in a unit starts failing an invariant Postgres
does not have. And the world fixture builds accounts directly, so `ck_users_scope` applies — a role
change in a test has to keep the matching scope column (a `user` row cannot simply be relabelled
`super_admin` while it still has a `unit_id`).

There is **no lint config** in this repo (Ruff is configured only through the JetBrains plugin). The
engine still has no automated tests: the REPL above is the fastest way to exercise retrieval and answer
generation without the API or a database, and `eval_questions.csv` (`question,expected` pairs) is a
committed fixture with no runner attached to it yet.

### Docker

`Dockerfile` builds the API and its `CMD` chains `alembic upgrade head && seed_from_xlsx && uvicorn`.
**`docker-compose.yml` is deliberately not in this repo** — it was moved to a separate deployment repo
(commit `bddbc38`), so the `docker compose` commands in `README.md` only work from there. The compose
setup it expects: a `postgres:16` service named `db`, `env_file: .env`, and named volumes for
`/root/.cache/huggingface` and `/srv/emb_cache` (both caches are expensive to lose).

#### Why the base image is pinned by digest (2026-08-14)

`FROM python:3.11-slim@sha256:a630a63c…`, not `FROM python:3.11-slim`. **The tag moves**: the official
Python images are rebuilt whenever Debian ships a security update, and this machine's build cache showed
three different digests of that one tag inside a month — `90edbeb8` (07-15), `3c35dbe0` (08-05),
`a630a63c` (08-14). A changed base layer invalidates every layer beneath it, so a rebuild days later
re-downloaded the 4.3 GB CUDA torch wheel with nothing in the repo having changed. That was the whole of
the "why does it download torch again" mystery; the pin is the fix, and everything else below is defence
in depth. Bump it deliberately — `docker pull python:3.11-slim` then
`docker image inspect python:3.11-slim --format '{{index .RepoDigests 0}}'` — never by accident.

Two smaller causes were fixed with it:

- **`COPY requirements.txt` used to sit above the torch install**, so every edit to that file invalidated
  the torch layer too — adding `jinja2` for the PDF reports cost a 4.3 GB re-download. The layers are now
  ordered by how often they change: base → apt → torch → `requirements.txt` → application code.
- **`--no-cache-dir` is gone**, replaced by `RUN --mount=type=cache,target=/root/.cache/pip`. A cache
  mount is not a layer, so the image does not grow — that was all `--no-cache-dir` was ever protecting —
  and a *deliberate* invalidation (a bumped base, a different `TORCH_INDEX`) now reuses the downloaded
  wheels instead of fetching them again. No `# syntax=` directive is needed for it; BuildKit's built-in
  frontend supports cache mounts, and pinning a frontend image would be one more thing to pull per build.

`.dockerignore` is new and is an **allow-list** (`*`, then the paths the Dockerfile copies). The context
was 6.1 GB — `venv/` alone is 6.0 GB — sent to the daemon on every build for the ~1 MB the image actually
takes. A deny-list would need editing every time the repo grew a directory; the cost of the allow-list is
that a new `COPY` needs a matching `!line`, and forgetting one fails the build loudly.

The build cache is still not immortal: BuildKit's default GC prunes cache mounts after 48 h and the rest
under disk pressure, and `docker builder prune` wipes all of it. What the pin guarantees is that nothing
is invalidated *by itself* between builds.

### Torch: pinned to a CUDA build, on purpose

The Dockerfile installs `torch==2.7.1` from a **PyTorch index before** `requirements.txt`, so that wheel
already satisfies `torch>=2.6,<2.8` and pip never resolves torch off PyPI on its own. Keep the ordering
when touching dependencies — the whole point of the line is that it wins the resolution, and the second
point is that torch is the slowest-changing layer and must sit above anything that churns.

The index is `ARG TORCH_INDEX`, defaulting to `.../whl/cu126` (the same build the checked-out `venv/`
has). The CPU-only image is one flag away:
`docker build --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cpu .`, worth ~4.3 GB of image
(torch 1.6 GB + `nvidia-*` 2.7 GB) on a host that will never have a GPU. cu126 covers Pascal through
Hopper; a Blackwell card needs cu128.

`job_qa_service/engine.py` picks its device from `_HAS_CUDA`, so the same image encodes on the GPU where
one is visible and on the CPU where it is not — nothing in the application changes, and a GPU-less host
just runs the timings in the CPU row of *Encoding cost* below. The GPU reaches the container only if the
host has `nvidia-container-toolkit` **and** the compose service reserves a device; that reservation
lives in the deploy repo's `docker-compose.yml`, and a host without the toolkit will refuse to start the
service at all rather than fall back — see *Encoding cost* for the install.

## Configuration — two independent paths

This is the most common source of confusion. Settings are read in two unrelated ways:

1. `app/config.py` — a pydantic-settings `Settings` object for the web layer.
2. `job_qa_service/config.py` — module-level `os.getenv(...)` constants (`OPENAI_API_KEY`,
   `OPENAI_BASE_URL`, `LLM_MODEL`, `EMBED_MODEL_NAME`, `EMB_CACHE_DIR`) evaluated **at import time**.
   The two files have deliberately similar names and nothing to do with each other.

Because of (2), everything must be present in the **real process environment**, and (1) does not try to
help: there is **no `env_file`** on `Settings` any more. Loading a `.env` there would satisfy the web
layer while the engine — which never sees it — went on without an API key, which is worse than not
loading it at all. (The old `Settings.Config.env_file = "../.env"` was a CWD-relative path resolving
outside the project, so nothing changed in practice when it went.) In deployment the compose `env_file`
injects the variables into the environment, which is what makes it work.

**Secrets are declared with `Field(...)` and no default, so a missing one is a startup crash.**
`JWT_SECRET` (≥32 chars, the RFC 7518 minimum for HS256), `OPENAI_API_KEY`, `ADMIN_USERNAME` and
`ADMIN_PASSWORD` (≥8) each stop the process while `app.config` is imported. `OPENAI_API_KEY` is required
even though the engine degrades gracefully without it — an engine with no key answers every question
from a fallback template, which looks like the service working. `load_settings()` catches the
ValidationError and re-raises a short `RuntimeError` naming the offending variables: a raw pydantic error
renders the *input* it was given, i.e. every collected environment variable, secrets included, into the
container log.

`DATABASE_URL` may be set directly, or it is assembled from `POSTGRES_USER`, `POSTGRES_PASSWORD`,
`DATABASE_HOST`, `DATABASE_PORT`, `POSTGRES_DB` (all now in `.env.example`) — with the credentials
percent-quoted, and a half-filled set raising instead of yielding
`postgresql+psycopg2://None:None@None:None/None` as it used to. The hardcoded `sqlalchemy.url` in
`alembic.ini` is always overridden by `alembic/env.py`, which pulls `settings.DATABASE_URL`.

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
  rebuild leaves the old one in place with the error recorded in `manager.last_result`. The two engines
  overlap in memory by design, which is why the encoder is shared and the embeddings are cached per text
  rather than per corpus — see *Embedding cache*.
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
POST   /accounts/{id}/organization   super_admin       move an organization's admin to another one
DELETE /accounts/{id}         any admin, over accounts below them
POST /orgs · GET /orgs · GET /orgs/{id} · PATCH /orgs/{id} · DELETE /orgs/{id}
                              super_admin (org_admin reads its own)
GET  /orgs/{id}/logo          same readers: the image, kept out of the list
POST /units · GET /units · GET /units/{id} · PATCH /units/{id} · DELETE /units/{id}
                              super_admin | org_admin
                                                 (a unit_admin only *reads* its own unit)
GET  /auth/me                 any account: role plus the organization/unit it sits in
POST /auth/password           any account: its own password, against the current one
GET  /stats                   super_admin | org_admin | unit_admin   dashboard counts, scoped as /accounts
POST /reports/search          any account: an answer it already has, printed as a PDF
POST /search · /search/advanced    any account: a question, or a profile ranked against the corpus
```

An org_admin deliberately **cannot** create ordinary users — it creates the units and their admins, and
those admins staff their own unit. A super_admin may stand in at any level, but must then name the target
(`organization_id`/`unit_id`) explicitly, since it has no scope of its own to default to.

`GET /stats` (`app/routers/stats.py`) is the dashboard's single call, and everything it returns is a
**count**. It reuses `accounts.visible_users` for the roster and repeats the units router's own narrowing,
so it can never total up something its caller was not entitled to list; an ordinary user has no dashboard
at all. Two numbers deliberately sit outside that scope because they describe the one shared corpus rather
than the caller's tenancy — `jobs.corpus_records` (every approved row) and `jobs.engine_records`
(`manager.record_count`, what the *running* engine was built from, `None` while none is loaded). The gap
between them is exactly what a rebuild would pick up, and the client draws it as such. `visible_users`
leaves the caller's own row out — an admin does not manage themselves — so the job scope adds their id
back, or an admin's own suggestions would be missing from their own queue. The `*_series` fields are one
count per calendar **day**; which Persian month a day belongs to is decided in the client, because
فروردین straddles two Gregorian months and a month bucketed here could only be relabelled wrongly there.

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
above them to undo it, and a unit_admin has nobody above them inside their unit. The one thing an account
does to itself goes through a different endpoint and a different proof — `POST /auth/password`, below.

`is_active` is checked in two places on purpose. `routers/auth.py:login` refuses a blocked account with
403 rather than the 401 given for a wrong password — the password *was* right, and the person needs to
know to ask their admin instead of retrying. `auth.py:get_current_user_optional` checks it again on every
request, so a token minted before the block dies with it instead of lasting out its hour. Blocking is not
inherited: blocking a unit_admin leaves that unit's users able to log in. A blocked account keeps its row,
its unit and everything it has suggested; nothing is deleted.

**Moving** (`POST /accounts/{id}/unit`) needs two permissions at once, and that pair is what keeps an
org_admin inside their organization: `assert_can_manage_account` over the account plus `assert_manages_unit`
over the destination. It is closed to a unit_admin on purpose — they run one unit, and a move is a decision
about two of them. Only accounts that live in a unit can move here (`user`, `unit_admin`; a super_admin
belongs to nothing and has nowhere to go), the role is unchanged by the move, and a unit_admin may only
land in a unit that has no admin — checked in `move_to_unit` so the answer is a 409 naming the sitting admin
rather than an IntegrityError from the partial unique index.

An **org_admin moves through `POST /accounts/{id}/organization`** instead, because it belongs to an
organization directly and `/unit` has nothing to offer it. Same shape, one level up: the role survives the
move, the destination must have no admin (a 409 naming them, not an IntegrityError), and re-submitting the
organization the account is already in is a no-op rather than a conflict against its own row. It is
**super-admin only**, and that is derived rather than declared: `assert_can_manage_account` already refuses
an org_admin every peer — a peer has no unit through which it could sit inside their organization — and an
org_admin has no second organization to move anyone into. Between the two endpoints, «ویرایش» of an account
is exactly *where it sits*: a role decides which scope column the row carries, and a username is the
credential.

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

**Editing** (`PATCH /orgs/{id}`, `PATCH /units/{id}`) takes the same authority as creating the container:
an organization is a super_admin's, a unit is its organization's admin's as well. A unit accepts `RenameIn`
and nothing else — a name is the only thing it has that is not a pointer to what it holds, so it never
changes organization here and never changes id. Uniqueness is re-asked the way creation asks it (globally
for an organization, inside the organization for a unit) but excluding the row itself, or renaming
something to what it is already called would 409.

An **organization** also carries a profile — `code`, `address`, `phone`, `email`, `logo` — added in
migration `0006` from the customer's `admin_panel.mp4` («شناسه سازمان», «آدرس سازمان», «شماره تماس»،
«پست الکترونیکی», «انتخاب لوگو»). Three things about it are deliberate and easy to undo by accident:

- **Every column is nullable, though the reference form marks four of them required.** The requirement is
  the form's (`components/manage/Forms.jsx:OrganizationDialog`), not the column's: the organizations that
  predate the migration have none of this, and `PATCH` is how they get filled in — an admin fixing a typo
  in a name cannot first be made to invent an email. A NOT NULL would have needed a backfill of invented
  values, which is worse data than an honest NULL.
- **`OrganizationUpdateIn` applies only the fields actually sent** (`exclude_unset`), so one dialog backs
  both the whole form and a one-box correction, and a field sent as `""` is *cleared*. Absent and empty
  therefore mean different things — which is what lets the client leave `logo` out of a PATCH that never
  opened the picker instead of wiping the image the row already has.
- **The logo is bytes in the row, `deferred`, and reached through `GET /orgs/{id}/logo`.** It is not in
  `OrganizationOut` — a list read by three pages would otherwise carry an image per row — so the row
  answers `has_logo` (read off the cheap `logo_mime`, which is why `apply_logo` is the only place either
  column is written). The endpoint returns a **data URI rather than raw bytes**, because every endpoint
  here needs the Authorization header and an `<img src>` cannot send one. `decode_logo` checks the
  declared mime against the file's own magic bytes and refuses SVG outright: the type decides how the
  bytes are served back to a browser, so believing it would make this field stored XSS.

Not implemented, and absent on purpose rather than forgotten: renaming an *account* — login is by username,
so the name is the credential.

**The two password endpoints are not variants of each other.** `POST /accounts/{id}/password` is an admin
setting somebody else's, and deliberately does not ask for the old one: it exists precisely for the account
that cannot supply it. `POST /auth/password` is an account changing its **own**, and requires the current
one. That field is what replaces the authority the admin had — a self-service reset without it would turn
a token left behind on a shared machine, good for an hour, into a permanent takeover. Wrong attempts spend
from the *login* budget on the same source+username key (`app/rate_limit.py`), because both are password
guesses against one account, and a correct one hands the budget back either way.

`/auth/password` is what closes the one gap in "nobody may act on their own account": every other role has
an admin above them who can reset their password, and a **super_admin has nobody**, so before this endpoint
existed the top of the hierarchy was the one account whose password could never be changed. It is open to
every role rather than only to the top one — the current password is what makes it safe, and that argument
does not depend on the caller's role. The client surfaces it twice for that reason: on the caller's own
row in `/manage/accounts`, and in the **header dropdown**, which is the only one of the two an ordinary
user can reach — and the only one an org_admin can, since `visible_users` reaches accounts through their
unit and an org_admin has none, so it never appears in its own listing.

### Rate limiting

`app/rate_limit.py` — a sliding window (one deque of timestamps per key) kept **in this process**. No
Redis and no new dependency, which is the right size for a deployment that is one uvicorn container; N
workers would mean N budgets, and the limits are loose enough that this is a detail rather than a hole.
Both limited endpoints answer `429` with a `Retry-After` header and a **Persian** `detail` — the one
place in `app/` that is not English, because the client prints a `detail` it has no case for verbatim
(`src/utils/errors.js`) and this one is read by the person standing at the login form.

The two are keyed differently because they are protecting different things:

- **`/auth/login`** — keyed on *source + username*, and only a **wrong** password spends
  (`check()` before the bcrypt comparison, `hit()` on failure, `reset()` on success). Guessing one
  account runs out of attempts; someone who signs in correctly all day never does. A username that does
  not exist is charged too, or the 401 that costs nothing would tell an attacker which names are worth
  working on. A blocked account's 403 spends nothing — the password was right. `POST /auth/password`
  spends from this same budget on the same key, on purpose: a wrong `current_password` is a password
  guess against that account like any other, and the two must not be one bucket each to walk between.
- **`/search`** — keyed on the *account* (`search_rate_limit` wraps `get_current_user`, so
  authentication comes first and an anonymous caller gets 401 rather than burning a budget). Per account
  and not per IP because a whole unit sits behind one office address, and the cost being capped is an
  encode plus an LLM call.

`TRUST_FORWARDED_FOR` is off by default: `X-Forwarded-For` is client-controlled, and trusting it blindly
hands out a fresh login budget per forged header. **It has to be turned on in the deployment**, where
nginx is the only route in — otherwise every client arrives as the proxy's single address and the whole
world shares one login key. When it is on, `client_ip` reads the **last** entry, not the first: nginx's
`$proxy_add_x_forwarded_for` appends the address it saw to whatever the caller sent, so the first entry
is the caller's own claim. Reading it would leave the header forgeable with the switch on, which is the
whole point of having the switch. `RATE_LIMIT_ENABLED` and the four count/window settings are read from
`settings` on every call, so they can be flipped in a test without rebuilding the limiters.

### Moderation flow

Users submit complete records to `POST /jobs/suggestions`, which land as `pending`. They are reviewed
under `/admin/*` (the whole router is gated by `dependencies=[Depends(require_super_admin)]`; `_review`
rejects any record that is not still `pending`). `POST /admin/jobs` inserts as `approved` directly.

**`PUT /admin/suggestions/{id}` is the third review action**, next to approve and reject: the reviewer
corrects the record before deciding on it, instead of rejecting it and asking the suggester to send it
again with one column fixed. It takes the whole `JobIn` body — the same ten-column form the suggester
filled in, so a partial body would only add a way for a column to go missing — and it is confined to
`pending` for the same reason `_review` is. An approved row is in the corpus every organization searches
and its text is what the embedding store is keyed on, so editing one *there* is a dataset edit plus a
rebuild rather than a review; a rejected one is closed. Both answer 409, as a second review does.
`tests/test_admin_suggestions_api.py` covers it (and is why `tests/conftest.py`'s `app` fixture now
mounts the admin router).

Moderation is **super-admin-only**, including for org and unit admins: approving a suggestion writes into
the one global corpus every organization searches, so it is not an organization-level decision. The
tenancy scopes accounts, not job records — there is a single shared dataset.

The first super admin is created from `ADMIN_USERNAME`/`ADMIN_PASSWORD` by the seed script, not by a
migration; every other account comes from the API. Migration `0003` maps the old single `admin` role onto
`super_admin`, so an existing deployment's admin keeps working.

### Advanced search: a profile instead of a question (2026-08-15)

`POST /search/advanced` is the other half of `/search`. There the user has a job in mind and wants prose
about it; here they list **what they can do**, column by column, and the corpus is ranked against it.
The unit is the *item* — one skill, one tool — and the answer is a ranking whose point is that it can
name which of the user's own items each job accounted for. It is job **analysis**, and it deliberately
cannot design a record: someone describing a job they *want* is still served by the free-text discovery
path on `/search`, which was left exactly as it was.

**Nothing new is encoded for it.** `profile.profile_query_text` writes the profile in the same shape
`engine._combined_text` writes a record — same labels, same «، » joins — so one query encode meets the
cached corpus vectors on their own terms. The dense channel is `emb_full` **alone**, without the
question path's `W_TITLE` half: a list of skills has nothing to say to a job title, and mixing it in
would be 40% noise. The other channel is `profile.coverage`, pure set arithmetic over tokens split once
at engine build (`engine.profile_tokens`), and the final order is `PROFILE_W_DENSE * dense +
PROFILE_W_COVER * coverage`. They are kept apart until that line because they fail differently — dense
understands that «حل مسئله» and «تحلیل مشکلات» are the same thing but cannot say *which* item it
honoured, and only what can be pointed at is ever shown to the user.

Coverage is computed for **all 1116 records**, not for a retrieved shortlist: it costs ~100 ms of set
operations, and a record matching every item the user typed must not be lost because dense ranked it
20th. Measured end to end on CPU: ~280 ms per request, of which 155 ms is the one encode.

Three decisions that are load-bearing, and what they were measured against:

- **An item matches by containment, not by overlap.** One shared token is not enough — «طراحی سیستم»
  and «طراحی لباس» share «طراحی» and are unrelated, and counting that would inflate every ratio on the
  page with matches the reader can see are wrong. One side has to be fully covered by the other, prefix
  matching in both directions so «برنامه‌نویس» reaches «برنامه‌نویسی».
- **…and failing that, by column.** Item-to-item alone reported that «مکانیک خودرو» is absent from
  «مکانیک‌ها و تکنسین‌های خدمات خودرو», because that record says «مهارت مکانیکی» in one member and «برق
  و انژکتور خودرو» in another and neither contains both words. The column is the unit the user is
  actually describing, so an item whose every word appears *somewhere* in the column counts too. The
  price is a compound item scattered across unrelated members; under-reporting was wrong on the very
  records the search exists to find, and this is the direction the error should lean.
- **`tools` is not a profile field.** 1099 of the 1116 tool cells are untranslated English
  (`AutoCAD | Revit | Adobe Acrobat`) — both sources, not just the O*NET rows — so a Persian item could
  never match one and the field would report a permanent 0%, with the analysis text then telling the
  person they lack every tool they listed. It stays in the suggestion form and in `_combined_text`,
  where the dense channel still reads it. **Translating that column is what would let the list grow by
  one line**, and is the single highest-value thing that could be done to this feature.

`PROFILE_DENSE_ONLY` (0.62) is the same kind of line as `DISCOVERY_FLOOR` and has the same limit: a
profile that matched **no item anywhere** still needs a strong dense score to count as a match at all,
because «تربیت اژدها / پرواز با جارو / عصای جادویی» measures 0.53 against «مربیان حیوانات» — dense
similarity is topical and cannot tell a real occupation from an invented one. Real profiles measure
0.65–0.75 on their own occupation even when the wording differs, which is the only gap the line can sit
in. Unlike the discovery path there is no LLM realism check here, and there does not need to be: this
path never creates anything, so the worst case is five jobs at 0% coverage, which is what the gate
turns into «هیچ شغلی … هم‌خوانی ندارد».

What a profile must carry is in `config.py` — `skills` with at least two items, and at least two fields
filled. `skills` because it is the column that most decides what an occupation *is*; a profile of tools
alone would match every job that happens to use a computer. The rule is enforced in `app/schemas.py`
(so the answer is a 422 naming the field) and repeated in the client (so the button is simply disabled
until it is met); the engine only projects onto its own field list and ignores anything else.

The report endpoint does **not** print these results — `POST /reports/search` takes a `SearchOut`, and a
ranking is a different document. That is the obvious next thing to add if anyone asks for it.

### PDF reports (`app/reports/`)

`POST /reports/search` prints a search answer as an A4 report: masthead, who issued it and when, the
question, the generated prose, then **every** column of the matched record — including the boxes the
user left folded, because a report is read away from the system that produced it.

The client **posts back the `SearchOut` it already has**, and the server does not re-run the search.
That is the load-bearing decision here: an answer is one LLM call, the model is not deterministic, and
a report that quietly disagreed with the page it was printed from would be worse than no report.
Nothing is stored — there is no report history, no table and no migration. The consequence is that the
payload is **untrusted input**: it is the caller's own answer coming back, but nothing proves this
server wrote it, so `schemas.ReportIn` is a separate type from `SearchOut` with every string bounded,
and the template autoescapes. Identity is the one thing never taken from the body — the masthead's
user/organization/unit come from the token, resolved as `GET /auth/me` does.

- **WeasyPrint, not a PDF-drawing library.** The document is Persian; Pango/HarfBuzz do the shaping and
  the right-to-left run order, where a canvas API would need every line reshaped and placed by hand.
  The price is four system libraries in the image — `libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b
  libcairo2` and friends, installed in the Dockerfile. **They are not optional**: without them the
  import fails and the whole API stops booting, not just this endpoint.
- **The font is shipped, not installed.** `assets/Vazirmatn-{Regular,Bold}.ttf` are loaded by
  `@font-face` against `render._ASSET_BASE`; the container has no fonts at all and fontconfig would
  fall back to a Latin face that draws Persian as boxes. The logo is the client's, downscaled to 240px.
- **Emoji are stripped** (`render.printable`, wired in as the Jinja `finalize`, so it covers every
  `{{ }}` including ones added later). The engine decorates its template answers with 📌 and 🔗
  (`job_qa_service/render.py`) and Vazirmatn has neither, so Pango substitutes a hex box — which does
  not even stay in its own block: a 🔗 heading the answer came out beside the *question* two elements
  above it. What counts as drawable is read from the font's own cmap, with ZWNJ and whitespace kept by
  hand since a cmap has no entry for them and dropping ZWNJ would rewrite «می‌شود» as «میشود».
- **No rate limit of its own.** `/search` is the expensive endpoint and is already capped per account;
  this one neither encodes nor calls the API, and what it can be made to render is bounded by the
  schema rather than by a budget. One render at a time all the same (`render._render_lock`):
  `@font-face` registration goes through process-global fontconfig state.
- The Persian date is `reports/jalali.py`, a dozen lines rather than a dependency — the server-side
  twin of the client's `src/utils/jalali.js`, which reaches the same calendar through `Intl`. Its clock
  is fixed at Iran's +03:30 rather than read from the container, which is UTC and would date every
  evening's report a day early.

### The client is a sibling repo

`../job_qa_frontend` (React + Vite + react-router, its own git repo; `vite.config.js` proxies `/api` to
`localhost:8000` in dev and nginx does it in production).

Its look and its architecture were both replaced to match a design the customer supplied — eight
reference `.tsx` files plus the logos and photographs, kept in the client repo's `style_files/`. That is
the source of truth for the chrome: a dark slate header, an RTL glass sidebar, `blue-50→indigo-100`
behind the content, and the login card over the campus photograph. The port brought the stack with it:
**Tailwind v4** (`@tailwindcss/vite`, no config file; `styles.css` is `@import "tailwindcss"` plus a
`@theme` naming Vazirmatn), **Redux Toolkit + redux-persist + RTK Query**, **react-hook-form** and
**react-hot-toast**, under the reference's own path aliases (`@components`, `@store`, `@services`,
`@hook`, `@utils`, `@constant`, `@assets`, `@routes`, `@pages`, declared in `vite.config.js`).
**recharts** was added later, for the dashboard only.

One chrome rule the reference does not have, applied everywhere rather than per page: **every form ends
with `ui/SubmitBar`** — a rule across the full width, then one green button under it: login, the job form
and the direct add. The provisioning forms now live in dialogs, where `ui/Modal`'s footer *is* that bar
(see *The management sections* below) rather than an exception to it. Green is reserved for *this* — the button that
files something — which is why it is a shade deeper than the `success` variant used by «تأیید» in the
moderation queue. The *rule* is what spans the form; the button is `size="lg" w-full max-w-md`, so it is
comfortably large (44px) but caps at 448px and sits at the start of the rule — the right, under
`dir="rtl"`. It was `w-full` at `size="xl"` to begin with, which on the ten-column job form drew a 1000px
green bar that read as a banner rather than a button. The cap is a maximum and not a fixed width on
purpose: the login card is narrower than it, so that form still ends in a button the width of its inputs.
Height and padding travel together as `Button`'s `size` prop rather than as a `className`, because
Tailwind emits `.h-8` before `.h-10` and a smaller height passed as a class silently lost to the base one.

The sidebar is the reference's own neutral slate glass (`bg-slate-500/35`), and the navigation states in
`layout/MenuItem.jsx` are the matching slate family. It was briefly retinted indigo to echo the
`blue-50→indigo-100` content behind it; the customer asked for the slate back, so the two files are once
again exactly what commit `f98125b` shipped. Change them together — the panel and the items in it are one
surface.

`src/services/*Api.js` is now the whole API surface — one `injectEndpoints` file per router here, over a
`fetchBaseQuery` that reads the token out of the store and clears the session on a 401. `axios` and the
old `src/api.js` / `src/auth.jsx` are gone; the session lives in `store/slices/authSlice.js` and is the
only thing persisted (the query cache deliberately is not).

Three places where the reference could not be followed literally, because this backend is not the one it
was written for: login posts JSON (`LoginIn`), not `FormData`; the sidebar gates on **role**
(`routes/roles.js:hasRole`) because there is no permission endpoint to fetch; and the header dropdown
shows username / role / organization / unit from `GET /auth/me`, because `UserOut` has no name, gender or
company to build the reference's profile links from. The reference's desktop dropdown is also anchored
`right-0`, which in RTL hangs it off the side of the page — it is anchored `left-0` here, as the
reference's own mobile header already does.

Under that summary the dropdown carries the two things an account does to *itself*: «تغییر رمز» and
«خروج از حساب», as `layout/UserMenu.jsx:AccountActions` — one copy for both header modes, which is the
fix for the pair having been written out twice in `DesktopMode`/`MobileMode` and drifting the moment
there was a second action. The dialog is `SelfPasswordDialog` and is owned by `Header` rather than by
either mode, so one of it exists whichever mode is drawn and it survives the breakpoint. This is the
only route to a password change for an ordinary user and for an org_admin — `/manage/accounts` is
admin-only, and an org_admin does not appear in its own listing there (`visible_users` reaches accounts
through their unit, and an org_admin has none). Opening it closes the dropdown: the panel is not modal
and would otherwise still be hanging open behind the dialog afterwards.

Two things in the client are coupled to this repo:

- `src/components/JobForm.jsx` holds the ten columns a second time, and backs both `POST /jobs/suggestions`
  and `POST /admin/jobs`. It is one of the places outside this repo that a column change has to reach.
  Its three prose columns are text boxes and **its seven list columns are `ui/ItemsInput`** — one item at
  a time, «+» to add — joined with «|» on submit and split back apart when a generated draft prefills the
  form. The «|» rule it used to state is gone from the page, because the user no longer types the
  separator: that was a guess they had to make («،» or «,» or «|»), and a wrong guess became one
  unsplittable cell that no search could match, on both sides of the system at once. Text pasted with a
  separator still in it is split rather than refused, and a space is deliberately not a separator —
  «حل مسئله» is one item.
- **Searching is one section with two modes.** `src/pages/Search.jsx` (`/search`) draws a
  `components/search/ModeSwitch` and mounts one of `components/search/QuestionSearch.jsx` (a sentence in,
  prose out) or `components/search/AdvancedSearch.jsx` (the six `PROFILE_FIELDS` as `ItemsInput`s, with
  `components/ProfileMatches.jsx` for the ranking). Advanced search was `/analyze` and its own sidebar
  item until the customer asked for one search entry with the advanced mode a button beside the plain
  one; `/analyze` now redirects to `/search?mode=advanced`, which is also where the switch keeps the
  mode — a query parameter rather than component state, so the mode is a link someone can send. Each
  match draws its coverage bar and then every item the user typed — matched ones in green, missed ones
  struck through — because the gap is half of what makes it an analysis. It reuses `JobDetails` for the
  record itself rather than growing a second way to render a job.
- **«/» is a redirect, not a page** (`App.jsx:Landing`, `src/routes/landing.js`): a session opens on the
  dashboard for anyone in `ADMIN_ROLES` and on `/search` for everyone else, which is why the search page
  moved off the root. `Login` sends the user to the same place, unless it is returning them to the page
  an expired session interrupted.
- `src/components/JobDetails.jsx` renders the `details` payload of a search result as one box per field,
  under the generated answer. It is deliberately *not* a third copy of the column list: labels, order and
  list-splitting all arrive from the backend, so a new content column shows up in the boxes on its own.
  The only thing it decides locally is shape — duties render as a bulleted list because they are
  sentences, other list columns as chips, prose as a paragraph. **Nothing folds**: every box was a
  `<details>` that had to be clicked open, with only the `primary` ones open to begin with, so the answer
  arrived under a row of closed labels and the reader had to guess which one held what they wanted. A
  record is short enough to print whole. `primary` still comes from the backend and still means
  something — those boxes are tinted and sorted first — but it decides *emphasis* now, not visibility.
  The grid is `1 / 2 / 3` fixed columns with `grid-auto-flow: dense`, not an `auto-fit` track: the boxes
  come in two widths (a chip column, and a full-width paragraph for prose and for the sentence list), and
  dense is what stops a full-width box leaving a hole behind it.
- **A generated job is edited where it is offered.** `QuestionSearch` sees `mode: job_generated` and
  renders `JobForm` prefilled from `job_draft` right there, under the answer, ending in the same green
  «ثبت پیشنهاد» that posts to `/jobs/suggestions` — plus a «نه، ممنون» that dismisses it. It used to be
  an accept button that stashed the draft in sessionStorage (`src/utils/draft.js`) and routed to
  `/suggest` to be edited and submitted there; the customer asked for one step fewer, so the stash and
  its two call sites are gone and `/suggest` is the blank form it always looked like. The proposal's
  `details` boxes are not drawn in this mode either — the editable form is already every column, and
  showing both was the same record twice.

- The **PDF report** is a «گزارش PDF» button on the result card, next to the match score, hidden for
  `out_of_domain`. It posts the result back (see *PDF reports* above), so `Search.jsx` keeps the question
  that produced the answer in its own `asked` state — the input box may already hold a retyped one, and
  the report prints the pair. `jobsApi.searchReport` has to spell out a `responseHandler`, since the body
  is a PDF; it branches on `response.ok` because a 401 or 422 arrives through the same handler and
  reading *that* as a blob would hide the `detail` string `utils/errors.js` prints. The file is named
  from the job title by `src/utils/download.js` rather than from `Content-Disposition` — RTK Query hands
  back only the body, and three reports downloaded in a row are told apart by job, not by a shared date.
  The backend still sets the header (`job-report-<jalali date>.pdf`) for anyone calling the endpoint
  directly.

- `src/pages/manage/` (`/manage/*`) is the provisioning chain, one section per route rather than one page
  stacking all of them: `dashboard`, `organizations`, `units`, `accounts`, each a child of `ManageLayout`
  and each gated on the same roles its endpoints are, so the sidebar never opens onto a section its caller
  cannot enter. They are **four top-level sidebar items**, not one «مدیریت» parent that expands — the
  customer asked for that, and `constant/menuItems.jsx` is the only file that decides it (`MenuItem.jsx`
  still knows how to draw a parent; nothing declares one). `ManageLayout` asks `GET /auth/me` **once** and
  hands the answer down through the outlet context, so no page re-derives the caller's organization or
  unit from the account list; it also *skips* `/orgs` for a unit_admin rather than catching the 403 it
  would answer with, and it draws nothing of its own — each page opens with its own `ui/PageToolbar`.
  `Dashboard` reads `/stats` alone and filters nothing for privacy — the server already scoped it, and the
  role only decides which panels are worth drawing (a unit_admin has one unit, so the two panels about
  units and organizations are absent instead of being a chart of one bar). `src/components/charts/` is
  recharts with `theme.js` holding the three fixed series hues, and `src/utils/jalali.js` turns the daily
  series into Persian months through `Intl` — no date library. Every mutation across the client
  invalidates the `Stats` tag, so the dashboard follows a provisioning change without refetching by hand.

- **The management sections are built on the customer's `admin_panel.mp4`** (in the client repo's
  `style_files/`, alongside the eight reference `.tsx`). Its pattern, and now theirs: a `ui/PageToolbar`
  whose one green «افزودن … جدید» button opens a dialog, a `ui/DataTable` under it, and a «عملیات‌ها»
  column of round `ui/IconButton`s that each open a dialog of their own. **Nothing is created or edited on
  the page any more** — `components/manage/Forms.jsx` is seven dialogs (`NameDialog`, `OrganizationDialog`,
  `CredentialsDialog`, `PasswordDialog`, `SelfPasswordDialog`, `ConfirmDialog`, `DetailsDialog`) rather than the cards it used
  to be. `NameDialog` is the units' now: an organization outgrew it when it gained a profile, and
  `OrganizationDialog` lays the six fields out two per row exactly as the reference does — a
  `grid-cols-2`, not an `auto-fit` track, which at this width silently becomes three columns and re-pairs
  every field with the wrong neighbour. Its logo picker is **three states, not two**: untouched leaves
  `logo` out of the PATCH body entirely, «حذف» sends `""`, a chosen file sends the data URI. Folding the
  first two together is the one change here that would quietly delete an image.
  - `ui/Modal` renders through a **portal onto `<body>`**: `MainLayout` scrolls the content area rather
    than the page, so a dialog declared inside a page would be clipped by that box. Its z-index has to
    clear the sidebar's `z-[9999999]` and stay under the toaster's `99999999`, so a failed submit reports
    itself over the dialog it failed in.
  - The **modal footer is the SubmitBar**, not an exception to it: a rule across the width with one green
    button at the start of it, moved to the bottom of the panel so it stays put while a long body scrolls.
    The button reaches its `<form>` through the HTML `form=` attribute. The reference puts its footer
    buttons at the *end* of the row; here they are at the start, because that is where the other eight
    forms in this app put theirs.
  - `RowActions` is `justify-end`, which under `dir="rtl"` anchors the group at the **left**, so delete is
    written last everywhere and keeps its position whatever else a row does or does not offer. Reading
    right to left the buttons come out edit, view, delete — the reference's own order.
  - `src/components/AccountsTable.jsx` carries the row actions — block/unblock, reset password, move,
    delete — each now a dialog rather than an inline panel. «ویرایش» on an account is *where it sits* and
    nothing else: a role decides which scope column the row carries, and a username is the credential you
    log in with, so neither has an endpoint. That makes the pencil **one dialog over two endpoints**,
    branching on the row's own scope column (`movingOrganization`): an org_admin picks an organization,
    everyone else a unit. It repeats the backend's rules in `canManage()`, `canMove()` and
    `canMoveOrganization()` purely so the table does not offer a button that would come back 403; the
    server is still the one enforcing them, and the pairs must be changed together.
  - The caller's **own row** carries one action of its own — the key, opening `SelfPasswordDialog`
    (current password + new, `POST /auth/password`). Every other button is hidden there because nobody may
    act on their own account, which is exactly why this one cannot reuse the admin reset. The same dialog
    hangs off the header dropdown for everybody (see *The client is a sibling repo* above); it is
    duplicated here because this is the page an admin is already on when they think about accounts, and a
    super_admin's own row is the one the thought usually starts from.
  - Organizations and units get a «تعریف ادمین» button as a **fourth row action**, shown only while the
    seat is empty. The page still does not try to predict whether a container is empty before deleting —
    it asks, and shows the 409 the server answers with.
  - There is no `/manage/users` page. Account creation of every kind is one dialog on `/manage/accounts`
    with a role picker, driven by the `CREATABLE` / `SCOPE_OF` tables at the top of `Accounts.jsx` — which
    are the provisioning chain of `app/routers/accounts.py` read as data. Organizations that already have
    an admin, and units that already have one, are left out of its picker rather than offered and refused.
    `/manage/users` redirects to `/manage/accounts` for anyone holding the old bookmark.

Self-registration is gone from the client too (the page was deleted, and every route but `/login` sits
inside `MainLayout` behind `Protected` now that `/search` needs a token).

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
| `profile.py` | advanced search: items in, coverage out — no embeddings of its own |
| `llm.py` | `LLMClient`: the chat call that returns `""` instead of raising |
| `render.py` | `build_context`, `template_one`/`template_two`, `render_draft`, `job_detail` |
| `emb_store.py` | `EmbeddingStore`: one cached vector per text, so a rebuild encodes only what changed |
| `engine.py` | `JobQAEngine`: corpus, embeddings, retrieval, the three answer paths |

`__init__.py` re-exports the public names and is where stdio is forced to UTF-8; `__main__.py` is the
REPL, so the entrypoint is `python3 -m job_qa_service`.

There are two public entry points. `analyze()` is the profile path — see *Advanced search* above; it
shares the corpus, the encoder and the cached vectors with everything below and adds no state beyond
`engine.profile_tokens`. `answer()` is the sentence path, and it branches on intent detection **before**
retrieval:

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
  job instead of asking about one facet of it. Those boxes are meant to be emphasized — the client tints
  them — and `job_detail` sorts them first so a client that ignores the flag still leads with the right
  field. It used to mean «open this one, fold the rest»; nothing folds in the client any more, so the
  flag is now about weight rather than about visibility.
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

### Embedding cache: one vector per text

`job_qa_service/emb_store.py` holds a `key -> vector` store in
`emb_cache/vectors_{model}.npz`, where the key is `sha256(EMBED_MODEL_NAME + text)`.
`engine._load_or_build_embeddings` asks it for the corpus and it encodes only the texts it has never
seen. **A rebuild after one approval encodes 2 texts, not 2232** — measured 2.5 s, against 258 s on GPU
and ~31 min on CPU before. Editing one record's description re-encodes that one text; a restart encodes
nothing.

Keying on content is what makes that safe, and it is the same property the previous per-corpus cache
had: a record whose text changed has a different key, so it misses automatically instead of serving
vectors built from text that no longer exists. Nothing is invalidated by hand, and the vectors are
bit-identical to the ones the old cache held — same model, same `normalize_embeddings=True` — so
retrieval and every threshold in `config.py` mean exactly what they meant before.

- The store is **two parallel arrays** (`keys`, `vectors`) in one npz, not one member per text: 2232 zip
  members are slow to write, two are not. Saves are atomic (temp file + `os.replace`), so a crash or a
  second process mid-write cannot leave a partial store.
- It **retains superseded vectors** — an edited record's old vector stays, so reverting the edit is free.
  It grows ~8 KB per record ever written, ~9.7 MB for this corpus; delete the file to compact it, at the
  cost of one full re-encode.
- `store.adopt_corpus_cache()` imports a matching pre-per-row `corpus_*.npz` if one is still in
  `EMB_CACHE_DIR` — the old file's name is a digest of all its texts, so a name match *is* proof the
  vectors belong to this corpus. Without it, the first boot after this change would re-encode a corpus
  it already had good vectors for (31 min on CPU) and throw the deployed volume's cache away. Once
  adopted, the `corpus_*.npz` files are dead weight and can be deleted.
- `POST /admin/rebuild` **no longer forces a re-encode**; `?force_embeddings=true` still does, for a
  store that has to be rewritten (a corrupted file, a changed encoder). `rebuild_async()` used to pass
  `rebuild_embeddings=True` unconditionally, which is what made every approval cost a full re-encode.
- `EMB_CACHE_DIR` is created at first use rather than at save time, so an unwritable cache dir fails
  before the encoding rather than after it.
- The Dockerfile does not copy `emb_cache/`; containers rely on the mounted volume.

**One encoder per process.** `engine.shared_model()` memoizes the `SentenceTransformer` on
`(model, device)`. A rebuild builds a whole new engine while the old one keeps serving, so loading it
per engine meant two copies of bge-m3 alive at once — 2.2 GB each in fp32. On CPU that only wasted RAM;
with the CUDA image it does not fit beside the old one on a 4 GB card and **the rebuild dies with CUDA
OOM** (observed on the dev GTX 1050). The weights are read-only and inference never mutates them, so
one copy serves both engines, and a rebuild no longer reloads the model either.

### Encoding cost: what was measured, and what each fix bought (2026-08-02)

Both fixes below are implemented. The measurements are kept because they are what the thresholds of the
argument rest on, and because the *before* column is what a regression would look like.

Measured on the dev machine (GTX 1050 4 GB, 8 CPU cores) over the real corpus — 1116 records, which is
2232 texts because every record is encoded twice (`_combined_text` + `_title_alias_text`):

| | one search query | full corpus re-encode |
|---|---|---|
| GPU | 22 ms | **258 s** (measured) |
| CPU | 155 ms | **~1874 s ≈ 31 min** (extrapolated from 200 texts) |

Two separate costs with two different fixes:

**A. Approving one suggestion cost a full re-encode** — O(n) work for an O(1) change. Fixed by the
per-row store (*Embedding cache* above): 2 texts, 2.5 s. This is the fix that mattered; a GPU alone
would have made the same wrong work 7× faster.

**B. Search latency** — every `/search` encodes the question. Fixed by the CUDA image: 155 ms → 22 ms,
per request.

Verified end to end against the real corpus before commit: the old cache is adopted with 0 re-encodes
and vectors bit-identical to it, a restart encodes nothing, a new record encodes 2 texts, an edited
description 1, `force_embeddings` still encodes all 2232 — and «وظایف افسر توپخانه چیست؟» still answers
from «افسران توپخانه و موشک» at 0.650, the same score as before the change.

**Still unknown about production:** which GPU it has and how much VRAM (bge-m3 wants ~2.2 GB in fp32,
and one encoder is now shared rather than one per engine — see *Embedding cache*), and whether the host
has `nvidia-container-toolkit`. The dev machine does **not** have the toolkit
(`docker info` lists only `runc`), so the compose device reservation has to be installed for before
`docker compose up` will start the api service there:
`sudo apt install nvidia-container-toolkit && sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker`.
None of that blocks the image: without a visible GPU the engine runs on CPU by itself.

A third possible reading of "slow build" — `docker build` itself — was a separate problem and was fixed
on 2026-08-14: see *Why the base image is pinned by digest*. Short version: the base tag moved under the
build, which invalidated the 4.3 GB torch layer for free.

## `data_extactor/` — offline data pipeline

One-off scripts, not part of the runtime. `aggrigation_script_for_jobs.py` aggregates the raw O*NET
xlsx files in `dataset_jobs/` into `onet_master_database_en.xlsx`; `aggregation_translated_jobs.py`
merges the per-batch Persian translations in `translated_xlsx/` and normalizes separators to `|`.
Note it writes to `../scripts/Merged_Occupations.xlsx` while the seed script and Dockerfile both expect
`Merged_Occupations.xlsx` at the repo root — the file has to be moved after regeneration. These scripts
use relative paths and must be run from inside `data_extactor/`. Their column set (`industry`, `level`,
`hard_skills`/`soft_skills`, …) is wider than the 8 columns the backend uses.
