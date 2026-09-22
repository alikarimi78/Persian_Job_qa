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
          "job": "افسران توپخانه و موشک"}


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


# The client no longer shows a match score, so none is served even if an engine sends one.
def test_no_match_score_is_served(world, client, monkeypatch):
    body = ask(client, monkeypatch, world.user_a1,
               {**STORED, "mode": "single", "score": 0.9, "scores": [0.9, 0.8]}).json()
    assert "score" not in body and "scores" not in body


def test_a_composed_answer_has_no_owner_field_to_misread(world, client, monkeypatch):
    body = ask(client, monkeypatch, world.user_a1,
               {**STORED, "mode": "job_generated",
                "job_draft": {"job_title": "رمال"}}).json()
    assert body["organization_id"] is None


class VocabularyEngine:
    def __init__(self):
        self.scopes = []

    def vocabulary(self, scope=None):
        self.scopes.append(scope)
        return {"skills": [{"text": "سخن گفتن", "count": 996}], "knowledge": []}


def read_vocabulary(client, monkeypatch, user=None):
    from src.engine_manager import manager
    engine = VocabularyEngine()
    monkeypatch.setattr(manager, "_engine", engine)
    headers = {"Authorization": f"Bearer {create_token(user)}"} if user else {}
    return engine, client.get("/search/vocabulary", headers=headers)


def test_the_vocabulary_comes_back_per_field(world, client, monkeypatch):
    _, response = read_vocabulary(client, monkeypatch, world.user_a1)
    assert response.status_code == 200
    assert response.json()["fields"] == {"skills": [{"text": "سخن گفتن", "count": 996}],
                                         "knowledge": []}


def test_the_vocabulary_is_drawn_from_the_callers_reach(world, client, monkeypatch):
    engine, _ = read_vocabulary(client, monkeypatch, world.user_a1)
    assert engine.scopes == [{None, world.user_a1.organization_id}]
    engine, _ = read_vocabulary(client, monkeypatch, world.root)
    assert engine.scopes == [None]


def test_the_vocabulary_needs_a_token(world, client, monkeypatch):
    _, response = read_vocabulary(client, monkeypatch)
    assert response.status_code == 401
