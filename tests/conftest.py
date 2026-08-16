"""Fixtures for the permission suite.

Three things have to happen before anything from `app` is imported, and all three are at
the top of this file rather than in a fixture:

1. the environment has to hold the variables `app/config.py` now *requires* — importing
   `app.config` without them is a ValidationError, which is the point of that change;
2. `DATABASE_URL` has to be sqlite, because `app/database.py` builds its engine at
   import time (it does not connect there, but the URL has to be one SQLAlchemy can
   parse without a Postgres driver reaching for a host that is not running);
3. `job_qa_service` gets a stub, because `app.routers.search` imports the engine package
   through `app.engine_manager`, and importing the real one loads torch and
   sentence-transformers — 20 seconds on every test run, for a package none of these
   tests exercise. The stub's `JobQAEngine` is never instantiated: the search tests set
   `manager._engine` to a canned object directly.

The database is one in-memory SQLite held open by a `StaticPool`, so the fixtures and
the request handlers see the same one. `PRAGMA foreign_keys=ON` is not the default in
SQLite and is what makes `ON DELETE SET NULL` (migration 0005 — a deleted account's job
suggestions survive it) actually testable here.
"""

import os
import sys
import types

os.environ.setdefault("JWT_SECRET", "test-secret-not-the-deployed-one-0123456789")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("ADMIN_USERNAME", "seed-admin")
os.environ.setdefault("ADMIN_PASSWORD", "seed-password")
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"

if "job_qa_service" not in sys.modules:
    _stub = types.ModuleType("job_qa_service")
    _stub.JobQAEngine = object
    _stub.__doc__ = "test stub; see tests/conftest.py"
    sys.modules["job_qa_service"] = _stub

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import create_token, hash_password
from app.database import Base, get_db
from app.models import Organization, Role, Unit, User
from app.routers import accounts as accounts_router
from app.routers import admin as admin_router
from app.routers import auth as auth_router
from app.routers import orgs as orgs_router
from app.routers import reports as reports_router
from app.routers import stats as stats_router
from app.routers import units as units_router

PASSWORD = "correct-horse-battery"
# bcrypt costs ~100 ms by design; the fixtures make a dozen accounts per test, so the
# one hash every account shares is computed once for the whole session.
_HASHED = hash_password(PASSWORD)


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _enforce_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


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
    """

    def __init__(self, db):
        self.db = db
        self.org_a = Organization(name="org-a")
        self.org_b = Organization(name="org-b")
        db.add_all([self.org_a, self.org_b])
        db.commit()

        self.unit_a1 = Unit(name="unit-a1", organization_id=self.org_a.id)
        self.unit_a2 = Unit(name="unit-a2", organization_id=self.org_a.id)
        self.unit_b1 = Unit(name="unit-b1", organization_id=self.org_b.id)
        db.add_all([self.unit_a1, self.unit_a2, self.unit_b1])
        db.commit()

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
        user = User(username=username, hashed_password=_HASHED, role=role,
                    organization_id=organization.id if organization else None,
                    unit_id=unit.id if unit else None)
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user


@pytest.fixture
def world(db) -> World:
    return World(db)


@pytest.fixture
def app(db) -> FastAPI:
    """The real routers, minus the ones these tests do not touch. `app.main` is not used
    because its lifespan loads the engine from a database that does not exist here."""
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
    api.dependency_overrides[get_db] = lambda: db
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
