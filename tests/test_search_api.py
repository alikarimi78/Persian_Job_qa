import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config import settings
from src.rate_limit import search_limiter
from src.security import create_token


class FakeEngine:
    def __init__(self, result):
        self.result = result

    def answer(self, question, scope=None):
        return self.result


@pytest.fixture
def client(db, monkeypatch):
    from src.routers import search as search_router

    api = FastAPI()
    api.include_router(search_router.router)
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)
    search_limiter._hits.clear()
    return TestClient(api)


def ask(client, monkeypatch, user, result):
    from src.engine_manager import manager
    monkeypatch.setattr(manager, "_engine", FakeEngine(result))
    return client.post("/search", json={"question": "وظایف افسر توپخانه چیست؟"},
                       headers={"Authorization": f"Bearer {create_token(user)}"})


STORED = {"intent": "responsibilities", "answer": "پاسخ آزمایشی",
          "job": "افسران توپخانه و موشک", "score": 0.9}


@pytest.mark.parametrize("mode", ["single", "job_match"])
def test_a_stored_answer_names_its_organization(world, client, monkeypatch, mode):
    owner = world.user_a1.organization_id
    body = ask(client, monkeypatch, world.user_a1,
               {**STORED, "mode": mode, "organization_id": owner}).json()
    assert body["organization_id"] == owner


@pytest.mark.parametrize("mode", ["single", "job_match"])
def test_a_public_answer_carries_no_organization(world, client, monkeypatch, mode):
    body = ask(client, monkeypatch, world.user_a1,
               {**STORED, "mode": mode, "organization_id": None}).json()
    assert body["organization_id"] is None


def test_a_composed_answer_has_no_owner_field_to_misread(world, client, monkeypatch):
    body = ask(client, monkeypatch, world.user_a1,
               {**STORED, "mode": "job_generated",
                "job_draft": {"job_title": "رمال"}}).json()
    assert body["organization_id"] is None
