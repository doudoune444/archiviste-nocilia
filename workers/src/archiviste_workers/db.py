"""asyncpg connection pool with pgvector codec registration."""

from __future__ import annotations

import asyncpg
from pgvector.asyncpg import register_vector

_ASYNC_PREFIX = "postgresql+asyncpg://"
_PG_PREFIX = "postgresql://"


def normalize_database_url(url: str) -> str:
    """Strip SQLAlchemy `+asyncpg` driver suffix; asyncpg expects libpq URIs."""
    if url.startswith(_ASYNC_PREFIX):
        return _PG_PREFIX + url[len(_ASYNC_PREFIX) :]
    return url


async def _init_connection(conn: asyncpg.Connection) -> None:
    await register_vector(conn)


async def create_pool(database_url: str, *, min_size: int = 1, max_size: int = 2) -> asyncpg.Pool:
    """Create an asyncpg pool with the pgvector codec installed on each connection."""
    return await asyncpg.create_pool(
        normalize_database_url(database_url),
        min_size=min_size,
        max_size=max_size,
        init=_init_connection,
    )
