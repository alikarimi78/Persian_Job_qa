"""Fixtures for the permission suite.

Three things have to happen before anything from `src` is imported, and all three are at
the top of this file rather than in a fixture:

1. the environment has to hold the variables `src/config.py` now *requires* — importing
   `src.config` without them is a ValidationError, which is the point of that change;
2. `DATABASE_URL` has to name the **test** database, because `src/database.py` builds its
   Prisma client from `settings.DATABASE_URL` at import time and every module under test
   shares that one client;
3. `job_qa_service` gets a stub, because `src.routers.search` imports the engine package
   through `src.engine_manager`, and importing the real one loads torch and
   sentence-transformers — 20 seconds on every test run, for a package none of these
   tests exercise. The stub's `JobQAEngine` is never instantiated: the search tests set
   `manager._engine` to a canned object directly.

## Why this needs a real Postgres now

The SQLAlchemy suite ran on one in-memory SQLite held open by a `StaticPool`, which cost
nothing and needed no server. That is not available under Prisma: the client is generated
against the `provider` named in `prisma/schema.prisma`, and there is no way to point a
postgresql client at a SQLite file. So the suite talks to a real database, and gains
something for the price — `ck_users_scope` and the two partial unique indexes are
Postgres objects written as raw SQL in `prisma/migrations/0001_init`, and SQLite only ever
imitated them through a `sqlite_where` twin in the model. What the tests exercise now is
the constraint the deployment actually has.

Point it somewhere with `TEST_DATABASE_URL`, or let it derive `<database>_test` from a
`DATABASE_URL` already in the environment (which is what makes
`set -a; . ../job_qa_deploy/.env; set +a` enough to run the suite). **The name must end
in `_test`**: every test truncates all four tables, and the guard below is what stands
between that and somebody's real database.
"""

import os
import subprocess
import sys
import types
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent

os.environ.setdefault("JWT_SECRET", "test-secret-not-the-deployed-one-0123456789")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("ADMIN_USERNAME", "seed-admin")
os.environ.setdefault("ADMIN_PASSWORD", "seed-password")


def _test_database_url() -> str:
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        return explicit
    source = os.environ.get("DATABASE_URL")
    if source:
        parts = urlsplit(source)
        return urlunsplit(parts._replace(path=parts.path.rstrip("/") + "_test"))
    user = os.environ.get("POSTGRES_USER", "postgres")
    password = os.environ.get("POSTGRES_PASSWORD", "postgres")
    host = os.environ.get("DATABASE_HOST", "localhost")
    port = os.environ.get("DATABASE_PORT", "5432")
    name = os.environ.get("POSTGRES_DB", "jobqa")
    return f"postgresql://{user}:{password}@{host}:{port}/{name}_test"


DATABASE_URL = _test_database_url()
if not urlsplit(DATABASE_URL).path.endswith("_test"):
    raise RuntimeError(
        f"Refusing to run the suite against {urlsplit(DATABASE_URL).path.lstrip('/')!r}: "
        "every test truncates all four tables, so the database name has to end in "
        "'_test'. Set TEST_DATABASE_URL.")
os.environ["DATABASE_URL"] = DATABASE_URL
# The five parts are no longer consulted once DATABASE_URL is set, but a stale
# DATABASE_HOST=db from a sourced deploy env would still confuse anything that reads
# them, so the derived URL is the single answer here.

if "job_qa_service" not in sys.modules:
    _stub = types.ModuleType("job_qa_service")
    _stub.JobQAEngine = object
    _stub.__doc__ = "test stub; see tests/conftest.py"
    sys.modules["job_qa_service"] = _stub

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.auth import create_token, hash_password
from src.database import connect, db as prisma, disconnect
from src.models import Role, User
from src.routers import accounts as accounts_router
from src.routers import admin as admin_router
from src.routers import auth as auth_router
from src.routers import orgs as orgs_router
from src.routers import reports as reports_router
from src.routers import stats as stats_router
from src.routers import units as units_router

PASSWORD = "correct-horse-battery"
# bcrypt costs ~100 ms by design; the fixtures make a dozen accounts per test, so the
# one hash every account shares is computed once for the whole session.
_HASHED = hash_password(PASSWORD)

# Children before parents is irrelevant under CASCADE, but the list is the schema's own
# order so that a table added to `schema.prisma` and forgotten here is easy to spot.
TABLES = ("jobs_info", "users", "units", "organizations")


@pytest.fixture(scope="session", autouse=True)
def database():
    """Rebuilds the test database from the migrations, once, then opens the client.

    `migrate reset` and not `db push`: `db push` writes what `schema.prisma` describes,
    and the CHECK constraint plus the two partial unique indexes exist only in the
    migration SQL — pushed instead of migrated, the suite would be testing a schema
    missing exactly the invariants it asserts.

    And `reset` rather than `deploy`, because a test database is disposable and a
    developer's is *not* a deployment: `deploy` refuses to touch a database whose history
    has diverged (an edited migration, a half-applied one), which is right in production
    and is a wall in front of `pytest`. Resetting drops the schema and replays every
    migration, so what the suite runs against is always exactly the history in the repo —
    and it is what proves that history still applies to an empty database. The `_test`
    guard above is what makes dropping safe. It creates the database if it does not exist
    yet, so a fresh checkout needs no `createdb`.
    """
    result = subprocess.run(
        [sys.executable, "-m", "scripts.prisma_cli", "migrate", "reset",
         "--force", "--skip-generate", "--skip-seed"],
        cwd=ROOT, env=os.environ, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            "Could not prepare the test database at "
            f"{urlsplit(DATABASE_URL)._replace(netloc='…').geturl()}\n"
            f"{result.stdout}\n{result.stderr}")
    connect()
    yield prisma
    disconnect()


@pytest.fixture
def db(database):
    """The client, against an empty database.

    Emptied before each test rather than after, so a failed run leaves its rows behind to
    be looked at. `RESTART IDENTITY` matters more than it looks: several tests assert on
    ids, and without it every test would see a different set of them.
    """
    prisma.execute_raw(
        f'TRUNCATE {", ".join(chr(34) + t + chr(34) for t in TABLES)} '
        "RESTART IDENTITY CASCADE")
    return prisma


class World:
    """Two organizations, three units, and one account of every role in each — enough
    for "may this caller touch that account" to have a wrong answer available at every
    level.

        org_a ── unit_a1 ── admin_a1 (unit_admin), user_a1, user_a1b
            │        └─ unit_a2 ── admin_a2 (unit_admin), user_a2
            └─ admin_a (org_admin)
        org_b ── unit_b1 ── admin_b1 (unit_admin), user_b1
            └─ admin_b (org_admin)
        root (super_admin, in neither)

    Built through the Prisma client rather than through `/accounts`, so `ck_users_scope`
    applies to every row here: a test that changes a role has to move the matching scope
    column with it, or the database refuses the write.
    """

    def __init__(self, db):
        self.db = db
        self.org_a = db.organization.create(data={"name": "org-a"})
        self.org_b = db.organization.create(data={"name": "org-b"})

        self.unit_a1 = db.unit.create(data={"name": "unit-a1",
                                            "organization_id": self.org_a.id})
        self.unit_a2 = db.unit.create(data={"name": "unit-a2",
                                            "organization_id": self.org_a.id})
        self.unit_b1 = db.unit.create(data={"name": "unit-b1",
                                            "organization_id": self.org_b.id})

        self.root = self.make_user("root", Role.super_admin)
        self.admin_a = self.make_user("admin-a", Role.org_admin, organization=self.org_a)
        self.admin_b = self.make_user("admin-b", Role.org_admin, organization=self.org_b)
        self.admin_a1 = self.make_user("admin-a1", Role.unit_admin, unit=self.unit_a1)
        self.admin_a2 = self.make_user("admin-a2", Role.unit_admin, unit=self.unit_a2)
        self.admin_b1 = self.make_user("admin-b1", Role.unit_admin, unit=self.unit_b1)
        self.user_a1 = self.make_user("user-a1", Role.user, unit=self.unit_a1)
        self.user_a1b = self.make_user("user-a1b", Role.user, unit=self.unit_a1)
        self.user_a2 = self.make_user("user-a2", Role.user, unit=self.unit_a2)
        self.user_b1 = self.make_user("user-b1", Role.user, unit=self.unit_b1)

    def make_user(self, username: str, role: Role, *, organization=None, unit=None) -> User:
        # `include` is not decoration: `assert_can_manage_account` decides an org_admin's
        # reach by reading `target.unit.organization_id`, and Prisma leaves an
        # un-included relation as None. An account built without it would look unit-less
        # to every rule in `accounts.py` — the same shape `accounts.get_account` loads.
        return self.db.user.create(
            data={"username": username, "hashed_password": _HASHED, "role": role,
                  "organization_id": organization.id if organization else None,
                  "unit_id": unit.id if unit else None},
            include={"unit": True})

    def reload(self, user: User) -> User:
        """The account as the database now holds it, with its unit loaded the way
        `accounts.get_account` loads it. Prisma models are plain values — nothing about
        them tracks the row, so a test that changed something re-reads it here instead of
        expecting the object it already has to follow along."""
        return self.db.user.find_unique(where={"id": user.id}, include={"unit": True})


@pytest.fixture
def world(db) -> World:
    return World(db)


@pytest.fixture
def app(db) -> FastAPI:
    """The real routers, minus the ones these tests do not touch. `main.py` is not used
    because its lifespan loads the engine, which these tests stub away.

    `get_db` is not overridden: the client the handlers reach for *is* the one this suite
    connected, because `src/database.py` builds it from `settings.DATABASE_URL` and the
    top of this file made that the test database before `src` was imported.
    """
    api = FastAPI()
    api.include_router(auth_router.router)
    api.include_router(accounts_router.router)
    api.include_router(orgs_router.router)
    api.include_router(units_router.router)
    api.include_router(stats_router.router)
    api.include_router(reports_router.router)
    # The moderation queue reaches `engine_manager` for the rebuild endpoints, which is
    # exactly what the stubbed `job_qa_service` at the top of this file is for — the
    # suggestion tests never touch the engine itself.
    api.include_router(admin_router.router)
    return api


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


@pytest.fixture
def as_user(client):
    """`as_user(account)` -> a callable that issues requests as that account.

    The token is minted rather than obtained from `/auth/login`, which keeps a dozen
    bcrypt comparisons out of every test; it goes through the same `get_current_user`
    the endpoints depend on, so the authorization path under test is untouched.
    """

    def _for(user: User):
        headers = {"Authorization": f"Bearer {create_token(user)}"}

        def request(method: str, url: str, **kwargs):
            return client.request(method, url, headers=headers, **kwargs)

        return request

    return _for
