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
        "every test truncates all three tables, so the database name has to end in "
        "'_test'. Set TEST_DATABASE_URL.")
os.environ["DATABASE_URL"] = DATABASE_URL

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

PASSWORD = "Correct-horse-battery1"
_HASHED = hash_password(PASSWORD)

TABLES = ("jobs_info", "users", "organizations")


@pytest.fixture(scope="session", autouse=True)
def database():
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
    prisma.execute_raw(
        f'TRUNCATE {", ".join(chr(34) + t + chr(34) for t in TABLES)} '
        "RESTART IDENTITY CASCADE")
    return prisma


class World:
    def __init__(self, db):
        self.db = db
        self.org_a = db.organization.create(data={"name": "org-a"})
        self.org_b = db.organization.create(data={"name": "org-b"})

        self.root = self.make_user("root", Role.super_admin)
        self.admin_a = self.make_user("admin-a", Role.org_admin, organization=self.org_a)
        self.admin_b = self.make_user("admin-b", Role.org_admin, organization=self.org_b)
        self.user_a1 = self.make_user("user-a1", Role.user, organization=self.org_a)
        self.user_a2 = self.make_user("user-a2", Role.user, organization=self.org_a)
        self.user_b1 = self.make_user("user-b1", Role.user, organization=self.org_b)

    def make_user(self, username: str, role: Role, *, organization=None) -> User:
        return self.db.user.create(
            data={"username": username, "hashed_password": _HASHED, "role": role,
                  "organization_id": organization.id if organization else None})

    def reload(self, user: User) -> User:
        return self.db.user.find_unique(where={"id": user.id})


@pytest.fixture
def world(db) -> World:
    return World(db)


@pytest.fixture
def app(db) -> FastAPI:
    api = FastAPI()
    api.include_router(auth_router.router)
    api.include_router(accounts_router.router)
    api.include_router(orgs_router.router)
    api.include_router(stats_router.router)
    api.include_router(reports_router.router)
    api.include_router(admin_router.router)
    return api


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


@pytest.fixture
def as_user(client):
    def _for(user: User):
        headers = {"Authorization": f"Bearer {create_token(user)}"}

        def request(method: str, url: str, **kwargs):
            return client.request(method, url, headers=headers, **kwargs)

        return request

    return _for
