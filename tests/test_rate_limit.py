"""`src/rate_limit.py`, and the two endpoints it guards.

The limiter is tested against an injected clock rather than by sleeping — a window is
five minutes, and a test suite that waits one out gets run by nobody.
"""

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.auth import create_token
from src.config import settings
from src.rate_limit import (RateLimiter, client_ip, login_key, login_limiter,
                            search_limiter)

from .conftest import PASSWORD


class Clock:
    """A monotonic clock that only moves when a test says so."""

    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def limiter(clock) -> RateLimiter:
    return RateLimiter(3, 60, "over budget, wait {seconds}", clock=clock)


@pytest.fixture(autouse=True)
def clean_limiters():
    """The two module-level limiters are process-wide state; tests must not inherit each
    other's tallies."""
    login_limiter._hits.clear()
    search_limiter._hits.clear()
    yield
    login_limiter._hits.clear()
    search_limiter._hits.clear()


# ---------- the limiter itself ----------

def test_checking_never_spends(limiter):
    """Which is what lets the login handler refuse before it has done any work, and
    charge afterwards only if the password was wrong."""
    for _ in range(20):
        limiter.check("a")


def test_a_budget_runs_out_after_exactly_its_limit(limiter):
    for _ in range(3):
        limiter.check("a")
        limiter.hit("a")
    with pytest.raises(HTTPException) as raised:
        limiter.check("a")
    assert raised.value.status_code == 429


def test_keys_have_separate_budgets(limiter):
    for _ in range(3):
        limiter.hit("a")
    with pytest.raises(HTTPException):
        limiter.check("a")
    limiter.check("b")


def test_the_refusal_says_how_long_to_wait(limiter, clock):
    for _ in range(3):
        limiter.hit("a")
    clock.advance(20)
    with pytest.raises(HTTPException) as raised:
        limiter.check("a")
    assert raised.value.headers["Retry-After"] == "40"
    assert "40" in raised.value.detail


def test_the_window_slides_rather_than_resetting(limiter, clock):
    limiter.hit("a")
    clock.advance(30)
    limiter.hit("a")
    limiter.hit("a")
    with pytest.raises(HTTPException):
        limiter.check("a")

    # the first hit falls out of the window; one slot opens, not the whole budget
    clock.advance(31)
    limiter.check("a")
    limiter.hit("a")
    with pytest.raises(HTTPException):
        limiter.check("a")


def test_a_reset_gives_the_whole_budget_back(limiter):
    for _ in range(3):
        limiter.hit("a")
    limiter.reset("a")
    for _ in range(3):
        limiter.spend("a")


def test_asking_about_a_key_does_not_create_one(limiter):
    """`check` reads; only `hit` writes. Otherwise shouting unknown usernames at the
    login endpoint would grow the dict for free between sweeps."""
    for name in range(100):
        limiter.check(f"user-{name}")
    assert limiter._hits == {}


def test_keys_nobody_touches_are_forgotten(limiter, clock):
    """Otherwise the dict grows by one entry per username ever guessed at."""
    limiter.hit("a")
    clock.advance(200)
    limiter.check("b")
    assert "a" not in limiter._hits


def test_a_limit_of_zero_means_unmetered(clock):
    unmetered = RateLimiter(0, 60, "never", clock=clock)
    for _ in range(50):
        unmetered.spend("a")


def test_the_whole_thing_can_be_switched_off(limiter, monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)
    for _ in range(50):
        limiter.spend("a")


# ---------- who the caller is ----------

class FakeRequest:
    def __init__(self, host="10.0.0.1", headers=None):
        self.client = type("Client", (), {"host": host})()
        self.headers = headers or {}


def test_the_connection_names_the_client_by_default(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_FORWARDED_FOR", False)
    request = FakeRequest("203.0.113.7", {"x-forwarded-for": "198.51.100.4"})
    assert client_ip(request) == "203.0.113.7"


def test_a_forwarded_header_is_ignored_unless_it_is_trusted(monkeypatch):
    """It is client-controlled: were it trusted by default, a forged header would hand
    out a fresh login budget on every request."""
    monkeypatch.setattr(settings, "TRUST_FORWARDED_FOR", False)
    forged = FakeRequest("203.0.113.7", {"x-forwarded-for": "1.1.1.1"})
    assert client_ip(forged) != "1.1.1.1"


def test_when_trusted_the_header_names_the_client_not_the_proxy(monkeypatch):
    """Behind nginx every connection is the proxy's, so without this the whole world
    shares one login budget."""
    monkeypatch.setattr(settings, "TRUST_FORWARDED_FOR", True)
    request = FakeRequest("172.18.0.2", {"x-forwarded-for": "203.0.113.7"})
    assert client_ip(request) == "203.0.113.7"


def test_a_trusted_header_is_read_from_the_right_end(monkeypatch):
    """nginx's `$proxy_add_x_forwarded_for` *appends* the address it saw to whatever the
    caller sent, so the first entry is the caller's own claim and the last is the only
    hop with evidence behind it. Reading the first would hand out a fresh budget per
    forged header even with the switch on."""
    monkeypatch.setattr(settings, "TRUST_FORWARDED_FOR", True)
    forged = FakeRequest("172.18.0.2", {"x-forwarded-for": "10.0.0.9, 203.0.113.7"})
    assert client_ip(forged) == "203.0.113.7"


def test_a_trusted_setup_with_no_header_falls_back_to_the_connection(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_FORWARDED_FOR", True)
    assert client_ip(FakeRequest("172.18.0.2")) == "172.18.0.2"


def test_the_login_key_is_source_and_username_together(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_FORWARDED_FOR", False)
    request = FakeRequest("10.0.0.1")
    assert login_key(request, "User-A1") == login_key(request, " user-a1 ")
    assert login_key(request, "user-a1") != login_key(request, "user-b1")


# ---------- POST /auth/login ----------

@pytest.fixture
def login_attempts(monkeypatch):
    """Three wrong passwords per account per source, so the tests stay readable."""
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(login_limiter, "limit", 3)
    return 3


def guess(client, username, password="wrong-password"):
    return client.post("/auth/login", json={"username": username, "password": password})


def test_guessing_one_account_runs_out_of_attempts(world, client, login_attempts):
    for _ in range(login_attempts):
        assert guess(client, "user-a1").status_code == 401
    refused = guess(client, "user-a1")
    assert refused.status_code == 429
    assert "Retry-After" in refused.headers
    # and the right password is refused too, for as long as the window lasts
    assert client.post("/auth/login", json={"username": "user-a1",
                                            "password": PASSWORD}).status_code == 429


def test_a_username_that_does_not_exist_still_costs_an_attempt(world, client, login_attempts):
    """Otherwise the 401 that costs nothing tells an attacker which names to work on."""
    for _ in range(login_attempts):
        assert guess(client, "does-not-exist").status_code == 401
    assert guess(client, "does-not-exist").status_code == 429


def test_each_account_has_its_own_budget(world, client, login_attempts):
    for _ in range(login_attempts):
        guess(client, "user-a1")
    assert guess(client, "user-a1").status_code == 429
    assert guess(client, "user-a1b").status_code == 401


def test_signing_in_is_never_rate_limited(world, client, login_attempts):
    """Only wrong passwords spend, so a person who signs in all day never runs out."""
    for _ in range(login_attempts * 4):
        assert client.post("/auth/login", json={"username": "user-a1",
                                                "password": PASSWORD}).status_code == 200


def test_getting_it_right_clears_the_tally(world, client, login_attempts):
    for _ in range(login_attempts - 1):
        guess(client, "user-a1")
    assert client.post("/auth/login", json={"username": "user-a1",
                                            "password": PASSWORD}).status_code == 200
    # back to a full budget rather than one mistake away from a refusal
    for _ in range(login_attempts):
        assert guess(client, "user-a1").status_code == 401


def test_a_blocked_account_does_not_spend_its_own_budget(world, db, client, login_attempts):
    """The password was right; the refusal is about the block, not about guessing."""
    db.user.update(where={"id": world.user_a1.id}, data={"is_active": False})
    for _ in range(login_attempts * 2):
        assert client.post("/auth/login", json={"username": "user-a1",
                                                "password": PASSWORD}).status_code == 403


def test_two_sources_guessing_the_same_account_are_counted_apart(world, app, login_attempts,
                                                                 monkeypatch):
    monkeypatch.setattr(settings, "TRUST_FORWARDED_FOR", False)
    one = TestClient(app, client=("203.0.113.7", 40000))
    other = TestClient(app, client=("198.51.100.99", 40000))
    for _ in range(login_attempts):
        assert guess(one, "user-a1").status_code == 401
    assert guess(one, "user-a1").status_code == 429
    assert guess(other, "user-a1").status_code == 401


# ---------- POST /search ----------

class FakeEngine:
    """Stands in for `JobQAEngine`, which these tests have no reason to load: what is
    under test is the ceiling in front of it."""

    def __init__(self):
        self.calls = 0

    def answer(self, question: str) -> dict:
        self.calls += 1
        return {"mode": "single", "intent": "description", "answer": "پاسخ آزمایشی",
                "job": "افسران توپخانه و موشک", "score": 0.9}


@pytest.fixture
def search_app(db, monkeypatch):
    from src.engine_manager import manager
    from src.routers import search as search_router

    api = FastAPI()
    api.include_router(search_router.router)
    engine = FakeEngine()
    monkeypatch.setattr(manager, "_engine", engine)
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(search_limiter, "limit", 3)
    api.state.engine = engine
    return api


def ask(client, user, question="وظایف افسر توپخانه چیست؟"):
    return client.post("/search", json={"question": question},
                       headers={"Authorization": f"Bearer {create_token(user)}"})


def test_search_is_capped_per_account(world, search_app):
    client = TestClient(search_app)
    for _ in range(3):
        assert ask(client, world.user_a1).status_code == 200
    refused = ask(client, world.user_a1)
    assert refused.status_code == 429
    assert refused.headers["Retry-After"]


def test_the_budget_belongs_to_the_account_not_to_the_address(world, search_app):
    """A whole organization behind one office IP must not share one allowance."""
    client = TestClient(search_app)
    for _ in range(3):
        ask(client, world.user_a1)
    assert ask(client, world.user_a1).status_code == 429
    assert ask(client, world.user_a2).status_code == 200


def test_admins_are_capped_too(world, search_app):
    client = TestClient(search_app)
    for _ in range(3):
        assert ask(client, world.root).status_code == 200
    assert ask(client, world.root).status_code == 429


def test_a_refused_search_never_reaches_the_engine(world, search_app):
    """The point of the ceiling: no encode, no LLM call."""
    client = TestClient(search_app)
    for _ in range(5):
        ask(client, world.user_a1)
    assert search_app.state.engine.calls == 3


def test_an_anonymous_caller_gets_401_and_spends_nothing(world, search_app):
    """Authentication comes first, so nobody can burn an account's budget from outside
    — or fill the limiter with keys by shouting at the endpoint."""
    client = TestClient(search_app)
    for _ in range(10):
        assert client.post("/search", json={"question": "چه خبر؟"}).status_code == 401
    assert ask(client, world.user_a1).status_code == 200
