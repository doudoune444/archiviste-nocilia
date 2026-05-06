"""FastAPI entry point for the workers service."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
import structlog
from fastapi import FastAPI

from archiviste_workers import __version__
from archiviste_workers.conversation.gcs_storage import (
    GcsConversationStorage,
    build_client,
)
from archiviste_workers.conversation.repository import ConversationRepository
from archiviste_workers.conversation.router import router as conversation_router
from archiviste_workers.embedder import Embedder
from archiviste_workers.retrieve.router import router as retrieve_router
from archiviste_workers.routers import health
from archiviste_workers.settings import Settings

logger = structlog.get_logger()


def _asyncpg_dsn(database_url: str) -> str:
    # asyncpg does not accept SQLAlchemy's `+asyncpg` scheme suffix.
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings()
    app.state.settings = settings

    pool = await asyncpg.create_pool(_asyncpg_dsn(settings.database_url))
    if pool is None:  # pragma: no cover - asyncpg returns a pool on success
        raise RuntimeError("failed to create asyncpg pool")
    app.state.db_pool = pool

    gcs_client = build_client(emulator_host=settings.gcs_emulator_host)
    storage = GcsConversationStorage(bucket_name=settings.gcs_bucket, client=gcs_client)
    app.state.gcs_storage = storage
    app.state.conversation_repo = ConversationRepository(pool)

    # RET-001 AC-6: load embedder once at startup; failure leaves state=None (AC-13).
    # Catch broad Exception: model download / OOM / file IO can surface various
    # exception classes from sentence-transformers; we never want to crash the boot.
    try:
        app.state.embedder = Embedder(settings.embedding_model)
    except Exception as exc:
        logger.warning("embedder_unavailable", error_type=type(exc).__name__)
        app.state.embedder = None

    logger.info("workers.startup", version=__version__, env=settings.env)
    try:
        yield
    finally:
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
