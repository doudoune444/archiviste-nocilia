"""asyncpg connection pool with pgvector codec registration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import asyncpg
from pgvector.asyncpg import register_vector

if TYPE_CHECKING:
    from archiviste_workers.auth_metadata.token import SqlTokenProvider

_ASYNC_PREFIX = "postgresql+asyncpg://"
_PG_PREFIX = "postgresql://"

# AC-5 (workers): max inactive lifetime (45 min) recycles connections before IAM token TTL (60 min).
_MAX_INACTIVE_CONNECTION_LIFETIME_SECS = 45 * 60


def normalize_database_url(url: str) -> str:
    """Strip SQLAlchemy `+asyncpg` driver suffix; asyncpg expects libpq URIs."""
    if url.startswith(_ASYNC_PREFIX):
        return _PG_PREFIX + url[len(_ASYNC_PREFIX) :]
    return url


async def _init_connection(conn: asyncpg.Connection) -> None:
    await register_vector(conn)


async def create_pool(
    database_url: str,
    *,
    token_provider: SqlTokenProvider | None = None,
    min_size: int = 1,
    max_size: int = 2,
) -> asyncpg.Pool:
    """Create an asyncpg pool with the pgvector codec installed on each connection.

    When token_provider is supplied (AC-6 / OQ-2 resolved), asyncpg receives an
    async callable as `password=`. asyncpg >= 0.21 invokes it per new physical
    connection — fresh IAM token injected without sharing state. Combined with
    max_inactive_connection_lifetime=45 min, connections are recycled before the
    IAM token TTL (60 min).
    """
    kwargs: dict[str, Any] = {
        "min_size": min_size,
        "max_size": max_size,
        "init": _init_connection,
    }
    if token_provider is not None:

        async def _password_cb() -> str:
            secret = await token_provider.get_or_refresh()
            return secret.get_secret_value()

        kwargs["password"] = _password_cb
        kwargs["max_inactive_connection_lifetime"] = _MAX_INACTIVE_CONNECTION_LIFETIME_SECS

    # asyncpg.create_pool returns Pool | None; raise explicitly so callers get a
    # clear error rather than a downstream AttributeError (MED-3 — no bare assert).
    pool = await asyncpg.create_pool(normalize_database_url(database_url), **kwargs)
    if pool is None:
        raise RuntimeError("asyncpg.create_pool returned None — check database_url and credentials")
    return pool
