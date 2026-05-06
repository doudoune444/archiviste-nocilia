"""Application settings via pydantic-settings."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "local"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/archiviste"

    # GCS_BUCKET is required (fail-fast at boot per AC-12, no default).
    gcs_bucket: str = Field(...)
    # When set, GCS client targets the emulator (fake-gcs-server). Unset in prod -> ADC.
    gcs_emulator_host: str | None = None

    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = "https://cloud.langfuse.com"

    embedding_model: str = "BAAI/bge-m3"
    default_chat_model: str = "claude-3-5-sonnet-20241022"
