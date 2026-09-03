import os
from urllib.parse import quote

from pydantic import Field, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    JWT_SECRET: str = Field(min_length=32)
    OPENAI_API_KEY: str = Field(min_length=1)
    ADMIN_USERNAME: str = Field(min_length=3)
    ADMIN_PASSWORD: str = Field(min_length=8)

    DATABASE_URL: str = ""
    POSTGRES_USER: str = ""
    POSTGRES_PASSWORD: str = ""
    POSTGRES_DB: str = ""
    DATABASE_HOST: str = ""
    DATABASE_PORT: int = 5432

    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60 * 24

    OPENAI_BASE_URL: str | None = None
    LLM_MODEL: str = "gpt-4o-mini"
    EMBED_MODEL_NAME: str = "BAAI/bge-m3"

    RATE_LIMIT_ENABLED: bool = True
    LOGIN_RATE_LIMIT: int = 10
    LOGIN_RATE_WINDOW: int = 300
    SEARCH_RATE_LIMIT: int = 20
    SEARCH_RATE_WINDOW: int = 60
    TRUST_FORWARDED_FOR: bool = False

    @model_validator(mode="after")
    def _assemble_database_url(self) -> "Settings":
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
        self.DATABASE_URL = (
            f"postgresql://{quote(self.POSTGRES_USER, safe='')}"
            f":{quote(self.POSTGRES_PASSWORD, safe='')}"
            f"@{self.DATABASE_HOST}:{self.DATABASE_PORT}/{self.POSTGRES_DB}")
        os.environ["DATABASE_URL"] = self.DATABASE_URL
        return self


def load_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in problem['loc']) or 'settings'}: {problem['msg']}"
            for problem in error.errors())
        raise RuntimeError(f"Invalid configuration — {problems}") from None


settings = load_settings()
