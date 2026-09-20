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

    # Lets scripts/create_staff.py's job be done through a one-time web form
    # (POST /crm/bootstrap-staff) instead of a local Python setup — useful
    # on Railway, where the client doesn't necessarily have Python + this
    # project's dependencies installed on their own machine. Empty/unset
    # means the endpoint is disabled outright (see require_bootstrap_secret
    # in app/api/routes/crm.py) — fail closed, not "anyone can create staff
    # accounts by default". Set a long random value in Railway's variables
    # only while creating accounts, then clear it again.
    staff_bootstrap_secret: str = ""

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