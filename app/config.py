from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+psycopg2://jobqa:jobqa@db:5432/jobqa"
    JWT_SECRET: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60 * 24

    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str | None = None
    LLM_MODEL: str = "gpt-4o-mini"
    EMBED_MODEL_NAME: str = "BAAI/bge-m3"

    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "change-me"

    class Config:
        env_file = ".env"


settings = Settings()
