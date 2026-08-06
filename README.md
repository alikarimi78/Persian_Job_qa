# Persian Occupation Q&A — FastAPI backend

A Persian-language Q&A service over an occupation dataset (O*NET, translated and
summarized). Ask it about a job and it answers from the record; describe a job you want
and it either finds the closest one or drafts a new record for an admin to approve.

Two halves, with no import between them in either direction:

| | |
|---|---|
| `app/` | FastAPI: auth, the organization → unit → user hierarchy, job moderation, `/search` |
| `job_qa_service/` | the RAG engine: retrieval (bge-m3 + BM25), intent detection, answer generation |

Everything the user reads is Persian; the dataset is ten columns per record (`job_title`,
`aliases`, `tools`, `skills`, `knowledge`, `abilities`, `work_context`,
`career_path_next`, `description`, `responsibilities`).

## Configuration

All settings come from the **real process environment** — see `.env.example` for the
full list. A `.env` file is deliberately not loaded: `job_qa_service/config.py` reads
`os.environ` at import time and would never see one, so a file that satisfied the web
layer alone would start an API whose engine has no API key. In deployment, compose's
`env_file` puts the variables in the environment.

`JWT_SECRET`, `OPENAI_API_KEY`, `ADMIN_USERNAME` and `ADMIN_PASSWORD` have **no
defaults**. Missing or malformed, they stop the process while `app.config` is being
imported, with a message naming each one (and printing none of their values):

```
RuntimeError: Invalid configuration — JWT_SECRET: Field required; OPENAI_API_KEY: Field required
```

`DATABASE_URL` may be given whole, or assembled from `POSTGRES_USER`,
`POSTGRES_PASSWORD`, `POSTGRES_DB`, `DATABASE_HOST` and `DATABASE_PORT`; a half-filled
set is an error rather than a URL full of `None`.

## Run

**Docker.** `docker-compose.yml` lives in the *deployment* repo, not here — run these
from there. The image's `CMD` chains `alembic upgrade head`, the seed script and
uvicorn.

```bash
cp .env.example .env           # fill it in; the API will not start without the secrets
docker compose up -d --build
docker compose exec api python -m scripts.seed_from_xlsx Merged_Occupations.xlsx
docker compose restart api     # the engine loads the seeded corpus at startup
```

The image installs a CUDA build of torch by default and encodes on the GPU where one is
visible, on the CPU where it is not. `--build-arg TORCH_INDEX=https://download.pytorch.org/whl/cpu`
builds the ~4.3 GB smaller CPU-only image.

**Locally**, against the checked-out `venv/` (Python 3.10, always from the repo root,
always as `python3 -m <tool>` — the console scripts in `venv/bin` carry a stale shebang):

```bash
venv/bin/python3 -m uvicorn app.main:app --reload --port 8000   # Swagger at /docs
venv/bin/python3 -m alembic upgrade head                        # apply migrations
venv/bin/python3 -m scripts.seed_from_xlsx Merged_Occupations.xlsx
OCCUPATIONS_PATH=Merged_Occupations.xlsx venv/bin/python3 -m job_qa_service  # engine REPL
```

## Tests

```bash
venv/bin/python3 -m pip install -r requirements-dev.txt
venv/bin/python3 -m pytest
```

`tests/` covers what is worth being sure of: the scope and permission rules
(`test_account_scope.py`, `test_accounts_api.py`, `test_tenancy_api.py`), what a token
does and does not prove (`test_auth.py`), the settings that must crash rather than
default (`test_config.py`), the two rate limits (`test_rate_limit.py`), and what the PDF
report puts on a page (`test_reports_api.py`). It runs against in-memory SQLite with a
stubbed engine, so it needs neither Postgres nor torch and finishes in seconds — but the
report tests do need WeasyPrint's `libpango`, the same system libraries the image
installs. The engine itself has no automated tests; the REPL above is how retrieval is
exercised.

## Architecture

**The corpus is the approved database rows.** `app/engine_manager.py` reads every
`jobs_info` row with `status = approved` into a DataFrame and hands it to the engine at
startup. Nothing reads the xlsx at request time — it is a seed input only.
`POST /admin/rebuild` builds a new engine on a background thread and swaps it in
atomically, so an approval becomes searchable without downtime; embeddings are cached
one vector per text (`emb_cache/`), so a rebuild after one approval encodes two texts
rather than the whole corpus.

**Two levels of tenancy, four roles**, each provisioned by the one above it. There is no
self-registration, and every endpoint but `/health` requires a token.

| role | scope | creates |
|---|---|---|
| `super_admin` | none — sees everything | organizations, their admins, further super admins |
| `org_admin` | one organization | that organization's units and their admins |
| `unit_admin` | one unit | the ordinary users of that unit |
| `user` | one unit | nobody — searches and suggests jobs |

Authorization is two checks, not one: `require_roles` decides who may call an endpoint,
and `accounts.assert_manages_*` / `assert_can_manage_account` decide which records they
may touch — an org_admin passes the first for `POST /units` and is still refused a unit
in someone else's organization. The rule for acting on an account (block, unblock, reset
password, move, delete) is the provisioning chain read downwards: **you may act on the
accounts you could have created, and never on your own**. The role and the blocked flag
are re-read from the database on every request, so a token minted before a change
carries none of the rights it had when it was signed.

The first super admin comes from `ADMIN_USERNAME` / `ADMIN_PASSWORD` via the seed
script; every other account is made through the API. Blocking sets `is_active = false`
and takes effect on existing tokens at once. Deleting is irreversible, but the job
records the account suggested stay in the corpus, unattributed. A unit can only be
deleted once it holds no accounts, an organization once it holds no units and no
accounts — the API answers 409 saying what is in the way rather than cascading.

**Moderation is super-admin-only**, including for org and unit admins: users submit
complete records to `POST /jobs/suggestions` as `pending`, and approving one writes into
the single corpus every organization searches, which is not an organization-level
decision.

**Rate limiting** (`app/rate_limit.py`) is a sliding window kept in this process — no
Redis, and the state is per worker. `/auth/login` is limited per source *and* username,
and only a wrong password spends from that budget, so guessing one account runs out of
attempts while someone who signs in correctly all day never does. `/search` is limited
per account, because that is where the cost is: one encode and one LLM call per
question. Both refuse with `429` and a `Retry-After` header. Behind a proxy, set
`TRUST_FORWARDED_FOR=true` — otherwise every client arrives as nginx's own address and
shares one login budget. The header's *last* entry is the one used, since nginx appends
the address it saw to whatever the caller claimed.

**PDF reports.** `POST /reports/search` turns an answer into an A4 Persian report
(`app/reports/`): the question, the generated prose, and every column of the matched
record. The client posts back the result it is already showing rather than naming a
question to re-run — that saves a second LLM call and guarantees the PDF matches the
page it came from. Nothing is stored. Rendering is WeasyPrint, so the image needs
`libpango`/`libcairo` (the Dockerfile installs them); without them the API will not
start at all.

**The client** is a sibling repo, `../job_qa_frontend` (React + Vite + RTK Query; its
dev server proxies `/api` here). `src/components/JobForm.jsx` holds the ten dataset
columns a second time — a column added here has to reach it too.

## Endpoints

| Method | Path | Access | Purpose |
|---|---|---|---|
| POST | /auth/login | public (rate limited) | JWT auth |
| GET | /auth/me | logged-in | role + organization/unit of the caller |
| POST | /search | logged-in (rate limited) | ask a question |
| POST | /reports/search | logged-in | print that answer as a PDF report |
| POST | /jobs/suggestions | logged-in | suggest a full record (pending) |
| GET | /jobs/suggestions/mine | logged-in | my suggestions + statuses |
| POST · GET · DELETE | /orgs, /orgs/{id} | super_admin (org_admin reads own) | organizations |
| POST · GET · DELETE | /units, /units/{id} | super_admin · org_admin | units of an organization |
| POST | /accounts/super-admins | super_admin | another super admin |
| POST | /accounts/org-admins | super_admin | an organization's single admin |
| POST | /accounts/unit-admins | super_admin · org_admin | a unit's single admin |
| POST | /accounts/users | super_admin · unit_admin | an ordinary user of a unit |
| GET | /accounts | super_admin · org_admin · unit_admin | accounts in the caller's scope |
| POST | /accounts/{id}/block · /unblock | any admin, downwards | refuse / restore login |
| POST | /accounts/{id}/password | any admin, downwards | set a new password |
| POST | /accounts/{id}/unit | super_admin · org_admin | move the account to another unit |
| DELETE | /accounts/{id} | any admin, downwards | delete it (its suggestions remain) |
| GET | /admin/suggestions | super_admin | list pending records |
| POST | /admin/suggestions/{id}/approve · /reject | super_admin | review |
| POST | /admin/jobs | super_admin | add a record directly (approved) |
| POST | /admin/rebuild | super_admin | rebuild the engine in the background |
| GET | /admin/rebuild/status | super_admin | rebuild progress / result |
| GET | /health | public | liveness + `engine_ready` |

## Migrations

```bash
venv/bin/python3 -m alembic revision --autogenerate -m "describe change"   # after editing app/models.py
venv/bin/python3 -m alembic upgrade head
```

Migration files live in `alembic/versions/`; review autogenerated ones before applying.
`alembic.ini`'s `sqlalchemy.url` is always overridden by `alembic/env.py`, which takes
`settings.DATABASE_URL`.
