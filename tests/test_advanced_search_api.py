import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.security import create_token
from src.config import settings
from src.rate_limit import search_limiter


class FakeEngine:
    def __init__(self):
        self.profiles = []

    def answer(self, question):
        return {"mode": "single", "intent": "description", "answer": "پاسخ آزمایشی",
                "job": "افسران توپخانه و موشک", "score": 0.9}

    def analyze(self, profile):
        self.profiles.append(profile)
        return {
            "mode": "profile_match", "intent": "profile",
            "answer": "تحلیل آزمایشی",
            "job": "خدمه پدافند ضدزره", "score": 0.61,
            "matches": [{
                "job_title": "خدمه پدافند ضدزره",
                "score": 0.61, "dense": 0.73, "coverage": 0.5,
                "fields": [{"key": "skills", "label": "مهارت‌ها و شایستگی‌ها",
                            "matched": ["هدف‌گیری"], "missing": ["رانندگی"],
                            "ratio": 0.5}],
                "detail": {"job_title": "خدمه پدافند ضدزره",
                           "fields": [{"key": "skills", "label": "مهارت‌ها و شایستگی‌ها",
                                       "value": "هدف‌گیری", "items": ["هدف‌گیری"],
                                       "primary": True}]},
            }],
        }


@pytest.fixture
def engine(monkeypatch):
    from src.engine_manager import manager
    fake = FakeEngine()
    monkeypatch.setattr(manager, "_engine", fake)
    return fake


@pytest.fixture
def search_app(db, monkeypatch):
    from src.routers import search as search_router

    api = FastAPI()
    api.include_router(search_router.router)
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)
    search_limiter._hits.clear()
    return api


@pytest.fixture
def client(search_app):
    return TestClient(search_app)


def ask(client, user, profile):
    return client.post("/search/advanced", json={"profile": profile},
                       headers={"Authorization": f"Bearer {create_token(user)}"})


VALID = {"skills": ["رانندگی", "هدف‌گیری"], "knowledge": ["سامانه‌های زرهی"]}


def test_a_profile_comes_back_ranked(world, engine, client):
    response = ask(client, world.user_a1, VALID)
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "profile_match"
    assert body["intent"] == "profile"
    assert body["matches"][0]["job_title"] == "خدمه پدافند ضدزره"


def test_every_match_carries_the_breakdown_and_the_record(world, engine, client):
    match = ask(client, world.user_a1, VALID).json()["matches"][0]
    assert match["fields"][0]["matched"] == ["هدف‌گیری"]
    assert match["fields"][0]["missing"] == ["رانندگی"]
    assert match["detail"]["job_title"] == "خدمه پدافند ضدزره"
    assert match["dense"] != match["coverage"]


def test_the_engine_is_handed_the_cleaned_profile(world, engine, client):
    ask(client, world.user_a1, {"skills": ["  رانندگی  ", "", "هدف‌گیری"],
                                "abilities": ["دقت", "   "]})
    assert engine.profiles[-1] == {"skills": ["رانندگی", "هدف‌گیری"],
                                   "abilities": ["دقت"]}


def test_an_unknown_field_is_refused_by_name(world, engine, client):
    response = ask(client, world.user_a1, {**VALID, "salary": ["زیاد"]})
    assert response.status_code == 422
    assert "salary" in response.text


def test_tools_is_not_a_profile_field(world, engine, client):
    assert ask(client, world.user_a1, {**VALID, "tools": ["آچار"]}).status_code == 422


@pytest.mark.parametrize("profile", [
    {"skills": ["رانندگی"], "knowledge": ["سامانه‌های زرهی"]},
    {"skills": ["رانندگی", "هدف‌گیری"]},
    {"knowledge": ["سامانه‌های زرهی"], "abilities": ["دقت"]},
    {},
])
def test_a_thin_profile_is_refused(world, engine, client, profile):
    assert ask(client, world.user_a1, profile).status_code == 422


def test_a_profile_that_is_only_blanks_is_thin_too(world, engine, client):
    assert ask(client, world.user_a1, {"skills": ["", "  "], "abilities": [""]}).status_code == 422


def test_the_item_count_is_capped(world, engine, client):
    assert ask(client, world.user_a1,
               {**VALID, "abilities": [f"مورد {n}" for n in range(21)]}).status_code == 422


def test_an_anonymous_caller_is_refused(world, engine, client):
    assert client.post("/search/advanced", json={"profile": VALID}).status_code == 401


def test_every_role_may_analyse(world, engine, client):
    for account in [world.root, world.admin_a, world.user_a1]:
        assert ask(client, account, VALID).status_code == 200, account.username


def test_it_shares_the_search_budget(world, engine, search_app, monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(search_limiter, "limit", 2)
    client = TestClient(search_app)
    assert ask(client, world.user_a1, VALID).status_code == 200
    assert client.post("/search", json={"question": "وظایف افسر توپخانه چیست؟"},
                       headers={"Authorization": f"Bearer {create_token(world.user_a1)}"}
                       ).status_code in (200, 503)
    refused = ask(client, world.user_a1, VALID)
    assert refused.status_code == 429
    assert refused.headers["Retry-After"]
    search_limiter._hits.clear()


def test_no_engine_is_503_not_500(world, client, monkeypatch):
    from src.engine_manager import manager
    monkeypatch.setattr(manager, "_engine", None)
    assert ask(client, world.user_a1, VALID).status_code == 503
