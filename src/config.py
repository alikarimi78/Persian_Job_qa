"""Settings for the web layer — read from the real process environment, and required.

This is *not* the engine's configuration: `job_qa_service/config.py` reads the same
variables straight out of `os.environ` at import time and knows nothing about this file.
The two are deliberately independent, which is why the secrets below are declared with
`Field(...)` and no default: a variable missing from the environment raises a
ValidationError while `src.config` is being imported — before uvicorn binds a port, and
long before a request is answered from a wrong value. The old form
(`JWT_SECRET: str = getenv("JWT_SECRET")`) did the opposite: an unset secret became
`None`, an unset database gave the string `postgresql://None:None@None:None/None`,
and the process started anyway.

There is now a third reader of the same variables, and it is the reason
`_assemble_database_url` writes its result back into `os.environ`: Prisma's query engine
and its CLI read `DATABASE_URL` out of the environment on their own, in a subprocess this
module cannot hand anything to. See the note there.

There is no `env_file` here on purpose, for the same reason. Loading a `.env` through
pydantic-settings would satisfy this layer while the engine — which reads
`os.environ` itself and never sees it — went on without an API key. Everything must be
in the real environment; in deployment the compose `env_file` puts it there.
"""

import os
from urllib.parse import quote

from pydantic import Field, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    # ---- secrets: no defaults, so a missing one stops the process ----
    # 32 bytes is the minimum RFC 7518 gives for HS256, and PyJWT warns below it
    JWT_SECRET: str = Field(min_length=32)
    # Required even though `job_qa_service` degrades gracefully without it: an engine
    # with no key answers every question from a plain-text template instead of the LLM,
    # which is the silent half-working state this module exists to prevent.
    OPENAI_API_KEY: str = Field(min_length=1)
    # Credentials of the first super admin, created by `scripts/seed_from_xlsx.py`.
    ADMIN_USERNAME: str = Field(min_length=3)
    ADMIN_PASSWORD: str = Field(min_length=8)

    # ---- database: either the whole URL, or the five parts it is built from ----
    DATABASE_URL: str = ""
    POSTGRES_USER: str = ""
    POSTGRES_PASSWORD: str = ""
    POSTGRES_DB: str = ""
    DATABASE_HOST: str = ""
    DATABASE_PORT: int = 5432

    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60 * 24

    # Mirrors of what `job_qa_service/config.py` reads on its own. Kept here so the
    # variables the engine needs are named in one place; nothing in `src/` reads them.
    OPENAI_BASE_URL: str | None = None
    LLM_MODEL: str = "gpt-4o-mini"
    EMBED_MODEL_NAME: str = "BAAI/bge-m3"

    # ---- rate limiting (src/rate_limit.py) ----
    RATE_LIMIT_ENABLED: bool = True
    # Wrong passwords for one account from one source, per window. Only failures count.
    LOGIN_RATE_LIMIT: int = 10
    LOGIN_RATE_WINDOW: int = 300
    # Searches per account per window — each one costs an encode and an LLM call.
    SEARCH_RATE_LIMIT: int = 20
    SEARCH_RATE_WINDOW: int = 60
    # Off by default because an X-Forwarded-For header is client-controlled and forging
    # it would hand out a fresh login budget per request. Turn it on only when a proxy
    # you control (the deployment's nginx) is the only way in, or every client collapses
    # into the proxy's single IP.
    TRUST_FORWARDED_FOR: bool = False

    @model_validator(mode="after")
    def _assemble_database_url(self) -> "Settings":
        """`DATABASE_URL` may be given whole; otherwise it is built from the five
        POSTGRES_*/DATABASE_* parts, and a missing part is an error rather than the
        literal `None` the old f-string produced.

        The scheme is plain `postgresql://`, **not** `postgresql+psycopg2://`. That
        suffix was SQLAlchemy's way of naming a driver; Prisma's query engine parses the
        URL itself and rejects a scheme it does not know, so a hand-written
        `DATABASE_URL` carried over from the Alembic days has to lose it.

        The assembled value is written back into `os.environ`, which the SQLAlchemy
        version had no reason to do. Prisma reads `DATABASE_URL` from the environment in
        two places this file cannot reach: the `prisma` CLI (`migrate deploy`, run at
        container start) and the query engine subprocess. `src/database.py` still passes
        the URL to the client explicitly — this is what makes
        `python -m scripts.prisma_cli …` work from the five parts alone.
        """
        if self.DATABASE_URL:
            os.environ["DATABASE_URL"] = self.DATABASE_URL
            return self
        parts = {"POSTGRES_USER": self.POSTGRES_USER,
                 "POSTGRES_PASSWORD": self.POSTGRES_PASSWORD,
                 "POSTGRES_DB": self.POSTGRES_DB,
                 "DATABASE_HOST": self.DATABASE_HOST}
        missing = [name for name, value in parts.items() if not value]
        if missing:
            raise ValueError(
                "DATABASE_URL is unset and cannot be assembled; missing: "
                + ", ".join(missing))
        # quoted: a password holding '@' or '/' would otherwise cut the URL in half
        self.DATABASE_URL = (
            f"postgresql://{quote(self.POSTGRES_USER, safe='')}"
            f":{quote(self.POSTGRES_PASSWORD, safe='')}"
            f"@{self.DATABASE_HOST}:{self.DATABASE_PORT}/{self.POSTGRES_DB}")
        os.environ["DATABASE_URL"] = self.DATABASE_URL
        return self


def load_settings() -> Settings:
    """Builds the settings, or stops the process with a message that says which variable
    is wrong and nothing else.

    The re-raise is not decoration: a pydantic ValidationError renders the *input* it
    was handed, which here is every environment variable it collected — `JWT_SECRET`
    and `ADMIN_PASSWORD` included — and that would go straight into the container log
    on a misconfiguration. `from None` drops that chained exception; the field names
    and the reasons survive, the values do not.
    """
    try:
        return Settings()
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in problem['loc']) or 'settings'}: {problem['msg']}"
            for problem in error.errors())
        raise RuntimeError(f"Invalid configuration — {problems}") from None


settings = load_settings()
