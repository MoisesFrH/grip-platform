from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings, loaded from environment variables / .env.

    Kept intentionally small for the MVP skeleton. Twilio, Gemini and
    per-tenant safety config are added as their own settings groups
    once those integrations are built (see architecture doc §9, §13, §17).
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"

    database_url: str = "postgresql+psycopg://grip:grip@localhost:5432/grip_platform"

    # Gemini — used only as the generation step behind explicit, restricted
    # function calling (architecture doc §9). Never called with free-form
    # tool-less generation for category A/B intents.
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    @field_validator("database_url")
    @classmethod
    def _use_psycopg_driver(cls, value: str) -> str:
        # Managed Postgres providers (Railway included) hand out a bare
        # "postgres://" or "postgresql://" URL — SQLAlchemy needs the
        # driver named explicitly for psycopg 3. Rewriting it here means
        # nobody has to hand-edit the provider's own DATABASE_URL value.
        if value.startswith("postgres://"):
            return "postgresql+psycopg://" + value[len("postgres://") :]
        if value.startswith("postgresql://"):
            return "postgresql+psycopg://" + value[len("postgresql://") :]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
