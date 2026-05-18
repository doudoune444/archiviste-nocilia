"""FastAPI entry point for the workers service."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from archiviste_workers import __version__
from archiviste_workers.conversation.gcs_storage import (
    GcsConversationStorage,
    build_client,
)
from archiviste_workers.conversation.repository import ConversationRepository
from archiviste_workers.conversation.router import router as conversation_router
from archiviste_workers.db import create_pool
from archiviste_workers.embedder import Embedder
from archiviste_workers.generate.router import router as generate_router
from archiviste_workers.retrieve.router import router as retrieve_router
from archiviste_workers.routers import health
from archiviste_workers.services.conversation_client import ConversationClient
from archiviste_workers.services.http_client import build_async_client
from archiviste_workers.services.llm import LlmClient
from archiviste_workers.services.query_log import QueryLogRepository
from archiviste_workers.services.retrieve_client import RetrieveClient
from archiviste_workers.settings import Settings

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings()
    app.state.settings = settings

    # RET-001 review HIGH: use db.create_pool so the pgvector codec is registered
    # on every connection. Raw asyncpg.create_pool would fail to encode list[float]
    # as `vector` for the retrieve SQL `c.embedding <=> $1` bind.
    pool = await create_pool(settings.database_url)
    app.state.db_pool = pool

    gcs_client = build_client(emulator_host=settings.gcs_emulator_host)
    storage = GcsConversationStorage(bucket_name=settings.gcs_bucket, client=gcs_client)
    app.state.gcs_storage = storage
    app.state.conversation_repo = ConversationRepository(pool)

    # AC-10 INFRA-002: Embedder() uses DEFAULT_MODEL_NAME ("mistral-embed") — do NOT
    # pass settings.embedding_model (stale "BAAI/bge-m3" default). Credentials are
    # picked up from MISTRAL_API_KEY / LLM_API_KEY env automatically.
    # Narrow except: only I/O and value errors are expected at construction time
    # (bad env, unreachable endpoint). Auth errors (401) surface at first call, not here.
    try:
        app.state.embedder = Embedder()
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
