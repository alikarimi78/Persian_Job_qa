"""`app/config.py`: a missing secret has to stop the process, not be papered over.

The failure this file locks down is the quiet one. `JWT_SECRET: str = getenv("JWT_SECRET")`
used to hand pydantic `None` when the variable was unset, and the five-part database URL
became the literal string `postgresql+psycopg2://None:None@None:None/None` — the app
started, signed tokens with a null key, and only fell over later and somewhere else.
"""

import pytest
from pydantic import ValidationError

from app.config import Settings, load_settings

SECRETS = ["JWT_SECRET", "OPENAI_API_KEY", "ADMIN_USERNAME", "ADMIN_PASSWORD"]
DATABASE_PARTS = ["POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB", "DATABASE_HOST",
                  "DATABASE_PORT"]
KNOWN = SECRETS + DATABASE_PARTS + ["DATABASE_URL", "OPENAI_BASE_URL", "LLM_MODEL",
                                    "EMBED_MODEL_NAME", "RATE_LIMIT_ENABLED",
                                    "LOGIN_RATE_LIMIT", "LOGIN_RATE_WINDOW",
                                    "SEARCH_RATE_LIMIT", "SEARCH_RATE_WINDOW",
                                    "TRUST_FORWARDED_FOR"]

COMPLETE = {"JWT_SECRET": "a" * 32,
            "OPENAI_API_KEY": "sk-test",
            "ADMIN_USERNAME": "admin",
            "ADMIN_PASSWORD": "password12",
            "DATABASE_URL": "postgresql+psycopg2://u:p@db:5432/jobs"}


@pytest.fixture
def env(monkeypatch):
    """Builds an environment from scratch, so a variable this developer happens to have
    exported cannot make a test pass."""

    def _set(**overrides):
        for name in KNOWN:
            monkeypatch.delenv(name, raising=False)
        for name, value in {**COMPLETE, **overrides}.items():
            if value is not None:
                monkeypatch.setenv(name, str(value))

    return _set


def test_a_complete_environment_is_accepted(env):
    env()
    settings = Settings()
    assert settings.JWT_SECRET == "a" * 32
    assert settings.DATABASE_URL == "postgresql+psycopg2://u:p@db:5432/jobs"


@pytest.mark.parametrize("secret", SECRETS)
def test_a_missing_secret_stops_the_process(env, secret):
    env(**{secret: None})
    with pytest.raises(ValidationError) as raised:
        Settings()
    assert secret in str(raised.value)


def test_an_empty_secret_counts_as_missing(env):
    """`.env.example` ships `OPENAI_API_KEY=` — a deployment that never filled it in
    would otherwise answer every question from the fallback template instead of the LLM,
    which looks like the service working."""
    env(OPENAI_API_KEY="")
    with pytest.raises(ValidationError):
        Settings()


def test_a_jwt_secret_too_short_to_sign_with_is_refused(env):
    env(JWT_SECRET="short")
    with pytest.raises(ValidationError):
        Settings()


def test_an_admin_password_too_short_for_the_api_is_refused(env):
    """`AccountIn` requires eight characters of every account made through the API; the
    seeded first super admin is held to the same bar."""
    env(ADMIN_PASSWORD="short")
    with pytest.raises(ValidationError):
        Settings()


# ---------- the database URL ----------

def test_the_url_is_assembled_from_its_parts_when_it_is_not_given(env):
    env(DATABASE_URL=None, POSTGRES_USER="jobs", POSTGRES_PASSWORD="secret",
        POSTGRES_DB="jobs_db", DATABASE_HOST="db", DATABASE_PORT=5433)
    assert Settings().DATABASE_URL == "postgresql+psycopg2://jobs:secret@db:5433/jobs_db"


def test_the_port_has_a_default_but_nothing_else_does(env):
    env(DATABASE_URL=None, POSTGRES_USER="jobs", POSTGRES_PASSWORD="secret",
        POSTGRES_DB="jobs_db", DATABASE_HOST="db", DATABASE_PORT=None)
    assert Settings().DATABASE_URL.endswith("@db:5432/jobs_db")


def test_a_password_with_punctuation_does_not_cut_the_url_in_half(env):
    env(DATABASE_URL=None, POSTGRES_USER="jobs", POSTGRES_PASSWORD="p@ss/word",
        POSTGRES_DB="jobs_db", DATABASE_HOST="db")
    assert Settings().DATABASE_URL == "postgresql+psycopg2://jobs:p%40ss%2Fword@db:5432/jobs_db"


def test_a_half_configured_database_names_what_is_missing(env):
    """The old form produced `...://None:None@None:None/None` and started anyway."""
    env(DATABASE_URL=None, POSTGRES_USER="jobs")
    with pytest.raises(ValidationError) as raised:
        Settings()
    message = str(raised.value)
    assert "POSTGRES_PASSWORD" in message and "DATABASE_HOST" in message


def test_an_explicit_url_wins_over_the_parts(env):
    env(POSTGRES_USER="ignored", POSTGRES_PASSWORD="ignored", POSTGRES_DB="ignored",
        DATABASE_HOST="ignored")
    assert Settings().DATABASE_URL == COMPLETE["DATABASE_URL"]


# ---------- what the crash itself says ----------

def test_the_crash_names_every_variable_that_is_wrong(env):
    env(JWT_SECRET=None, ADMIN_PASSWORD="short")
    with pytest.raises(RuntimeError) as raised:
        load_settings()
    message = str(raised.value)
    assert "JWT_SECRET" in message and "ADMIN_PASSWORD" in message


def test_the_crash_does_not_print_the_secrets_it_was_given(env):
    """A raw ValidationError renders the input it was handed — which is every collected
    environment variable — into the container log. This is why `load_settings` re-raises."""
    env(JWT_SECRET=None)
    with pytest.raises(RuntimeError) as raised:
        load_settings()
    message = str(raised.value)
    assert COMPLETE["OPENAI_API_KEY"] not in message
    assert COMPLETE["ADMIN_PASSWORD"] not in message
    assert COMPLETE["DATABASE_URL"] not in message


def test_a_good_environment_still_loads_through_the_same_door(env):
    env()
    assert load_settings().ADMIN_USERNAME == "admin"


# ---------- where the values may come from ----------

def test_a_dotenv_file_is_not_read(env, tmp_path, monkeypatch):
    """Deliberate: `job_qa_service/config.py` reads `os.environ` directly and would
    never see a file loaded here, so a `.env` that satisfied this layer alone would
    start an API whose engine has no API key. Everything goes in the real environment —
    in deployment, through compose's `env_file`."""
    env(JWT_SECRET=None)
    (tmp_path / ".env").write_text("JWT_SECRET=" + "b" * 32 + "\n")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValidationError):
        Settings()


def test_unrelated_variables_in_the_environment_are_ignored(env, monkeypatch):
    """The process environment also holds the engine's own variables (EMB_CACHE_DIR,
    OCCUPATIONS_PATH, HF_TOKEN...) and whatever else the host exports."""
    env()
    monkeypatch.setenv("EMB_CACHE_DIR", "/srv/emb_cache")
    monkeypatch.setenv("SOME_UNRELATED_THING", "1")
    assert Settings().JWT_SECRET == "a" * 32


# ---------- rate limiting knobs ----------

def test_the_rate_limits_have_working_defaults(env):
    env()
    settings = Settings()
    assert settings.RATE_LIMIT_ENABLED is True
    assert settings.LOGIN_RATE_LIMIT > 0 and settings.SEARCH_RATE_LIMIT > 0
    # off by default: an X-Forwarded-For header is client-controlled
    assert settings.TRUST_FORWARDED_FOR is False


def test_the_rate_limits_can_be_tuned_from_the_environment(env):
    env(RATE_LIMIT_ENABLED="false", SEARCH_RATE_LIMIT=5, TRUST_FORWARDED_FOR="true")
    settings = Settings()
    assert settings.RATE_LIMIT_ENABLED is False
    assert settings.SEARCH_RATE_LIMIT == 5
    assert settings.TRUST_FORWARDED_FOR is True
