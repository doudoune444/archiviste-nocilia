"""Application settings via pydantic-settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "local"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/archiviste"
    gcs_bucket_conversations: str = "archiviste-conversations-dev"

    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = "https://cloud.langfuse.com"

    embedding_model: str = "intfloat/multilingual-e5-base"
    default_chat_model: str = "claude-3-5-sonnet-20241022"
