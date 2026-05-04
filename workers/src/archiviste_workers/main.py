"""FastAPI entry point for the workers service."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from archiviste_workers import __version__
from archiviste_workers.routers import health
from archiviste_workers.settings import Settings

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings()
    app.state.settings = settings
    logger.info("workers.startup", version=__version__, env=settings.env)
    yield
    logger.info("workers.shutdown")


app = FastAPI(
    title="Archiviste Nocilia — Workers",
    version=__version__,
    lifespan=lifespan,
)

app.include_router(health.router)
