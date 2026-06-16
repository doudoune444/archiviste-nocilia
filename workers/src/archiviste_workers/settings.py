"""Application settings via pydantic-settings."""

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

LlmProvider = Literal["mistral", "anthropic", "google", "openai", "deepseek"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "local"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/archiviste"

    # SEC-005: when true, the asyncpg pool authenticates with a Cloud SQL IAM
    # access token fetched from the GCP metadata server (Cloud Run). Default false
    # so local/docker-compose/CI boot with password auth never hits the metadata
    # server (unreachable off-GCP → boot crash). Cloud Run sets this true via Terraform.
    cloud_sql_iam_auth: bool = False

    # GCS_BUCKET is required (fail-fast at boot per AC-12, no default).
    gcs_bucket: str = Field(...)
    # When set, GCS client targets the emulator (fake-gcs-server). Unset in prod -> ADC.
    gcs_emulator_host: str | None = None

    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = "https://cloud.langfuse.com"

    # AC-10 INFRA-002: default is "mistral-embed". BAAI/bge-m3 self-host = V2 (cf vision.md Q7).
    # Note: main.py constructs Embedder() without this field — the constant DEFAULT_MODEL_NAME
    # in embedder.py is the single source of truth for the model name.
    embedding_model: str = "mistral-embed"
    default_chat_model: str = "claude-3-5-sonnet-20241022"

    # GEN-001: LLM wrapper config-driven (AC-8/10).
    # AC-10 INFRA-002: llm_api_key doubles as Mistral embed key (shared key, cf vision.md Q7).
    # In prod the env var LLM_API_KEY is injected from Secret Manager MISTRAL_API_KEY.
    llm_provider: LlmProvider | None = None
    llm_model: str | None = None
    llm_api_key: SecretStr | None = None

    # GEN-001: internal worker -> worker base URL for /v1/retrieve and ING-003.
    workers_internal_base_url: str = "http://localhost:8000"
    conversation_internal_base_url: str = "http://localhost:8000"

    # MEM-002: token budget for the bounded conversation-memory window injected
    # into generation. Recent turns are accumulated newest-first until this many
    # Mixtral tokens, then injected as alternating Human/AI messages. Env-tunable
    # (MEMORY_TOKEN_BUDGET) so cost vs coherence is adjustable without a deploy.
    memory_token_budget: int = 2000
