"""FastAPI entry point for the workers service."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
import structlog
from fastapi import FastAPI

from archiviste_workers import __version__
from archiviste_workers.auth_metadata.token import SqlTokenProvider, TokenFetchError
from archiviste_workers.contradiction.router import router as contradiction_router
from archiviste_workers.conversation.gcs_storage import (
    GcsConversationStorage,
    build_client,
)
from archiviste_workers.conversation.repository import ConversationRepository
from archiviste_workers.conversation.router import router as conversation_router
from archiviste_workers.db import create_pool
from archiviste_workers.embedder import build_embedder
from archiviste_workers.generate.router import router as generate_router
from archiviste_workers.generate.stream_router import stream_router
from archiviste_workers.retrieve.router import router as retrieve_router
from archiviste_workers.routers import health
from archiviste_workers.services.conversation_client import ConversationClient
from archiviste_workers.services.http_client import build_async_client
from archiviste_workers.services.llm import LlmClient
from archiviste_workers.services.query_log import QueryLogRepository
from archiviste_workers.services.retrieve_client import RetrieveClient
from archiviste_workers.settings import Settings

logger = structlog.get_logger()


def _classify_pool_init_error(exc: BaseException) -> str:
    """Map a pool-init exception to an AC-10 reason_code string.

    reason_code ∈ {"metadata_token_failed","cloud_sql_auth_failed","timeout","network"} (AC-10).
    TokenFetchError carries .reason_code at raise-site (MED-2 — no substring matching).
    """
    if isinstance(exc, TokenFetchError):
        return exc.reason_code
    if isinstance(exc, asyncpg.PostgresError):
        return "cloud_sql_auth_failed"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, OSError):
        return "network"
    return "cloud_sql_auth_failed"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings()
    app.state.settings = settings

    # SEC-005: on Cloud Run, fetch IAM token at boot (fail-fast); pass provider so
    # each new physical connection receives a fresh token (asyncpg password= callable,
    # OQ-2). Off-GCP (local/docker-compose/CI) the metadata server is unreachable, so
    # cloud_sql_iam_auth defaults false → password auth from DATABASE_URL, no provider.
    # RET-001: create_pool registers pgvector codec on every connection.
    sql_token_provider = SqlTokenProvider() if settings.cloud_sql_iam_auth else None
    try:
        pool = await create_pool(settings.database_url, token_provider=sql_token_provider)
    except (TokenFetchError, asyncpg.PostgresError, OSError, TimeoutError) as exc:
        reason = _classify_pool_init_error(exc)
        logger.error("boot.sql_pool_init_failed", reason_code=reason, phase="boot")
        raise
    app.state.db_pool = pool
    app.state.sql_token_provider = sql_token_provider

    gcs_client = build_client(emulator_host=settings.gcs_emulator_host)
    storage = GcsConversationStorage(bucket_name=settings.gcs_bucket, client=gcs_client)
    app.state.gcs_storage = storage
    app.state.conversation_repo = ConversationRepository(pool)

    # AC-10 INFRA-002: build_embedder reads EMBEDDER_PROVIDER env (default "mistral").
    # EMBEDDER_PROVIDER=fake → FakeEmbedder (CI offline, no API call, deterministic).
    # EMBEDDER_PROVIDER=mistral → Embedder() with mistral-embed + MISTRAL_API_KEY.
    # Invalid provider → ValueError raised immediately (fail-fast, not swallowed).
    # Narrow except: only I/O errors at construction time (bad env, unreachable endpoint).
    # Auth errors (401) surface at first call, not here.
    try:
        app.state.embedder = build_embedder()
    except (ValueError, OSError) as exc:
        logger.warning("embedder_unavailable", error_type=type(exc).__name__)
        app.state.embedder = None

    http_client = build_async_client()
    app.state.http_client = http_client
    app.state.retrieve_client = RetrieveClient(http_client, settings.workers_internal_base_url)
    app.state.conversation_client = ConversationClient(
        http_client, settings.conversation_internal_base_url
    )
    app.state.query_log_repo = QueryLogRepository(pool)
    # AC-8: fail-fast at boot if LLM_* env missing/invalid.
    app.state.llm_client = LlmClient.from_env()

    logger.info("workers.startup", version=__version__, env=settings.env)
    try:
        yield
    finally:
        await http_client.aclose()
        await pool.close()
        if sql_token_provider is not None:
            await sql_token_provider.aclose()
        logger.info("workers.shutdown")


app = FastAPI(
    title="Archiviste Nocilia — Workers",
    version=__version__,
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(conversation_router)
app.include_router(retrieve_router)
app.include_router(generate_router)
app.include_router(stream_router)
app.include_router(contradiction_router)
