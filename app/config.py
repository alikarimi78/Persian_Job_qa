from pydantic_settings import BaseSettings
from os import getenv

class Settings(BaseSettings):
    DATABASE_URL: str = f"postgresql+psycopg2://{getenv('POSTGRES_USER')}:{getenv('POSTGRES_PASSWORD')}@{getenv('DATABASE_HOST')}:{getenv('DATABASE_PORT')}/{getenv('POSTGRES_DB')}"
    JWT_SECRET: str = getenv('JWT_SECRET')
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60 * 24

    OPENAI_API_KEY: str = getenv("OPENAI_API_KEY")
    OPENAI_BASE_URL: str | None = getenv("OPENAI_BASE_URL")
    LLM_MODEL: str = getenv("LLM_MODEL", "gpt-4o-mini")
    EMBED_MODEL_NAME: str = getenv("EMBED_MODEL_NAME", "BAAI/bge-m3")

    ADMIN_USERNAME: str = getenv("ADMIN_USERNAME")
    ADMIN_PASSWORD: str = getenv("ADMIN_PASSWORD")

    class Config:
        env_file = "../.env"


settings = Settings()
