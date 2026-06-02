"""ACL-agnostic cosine top-K SQL against `chunks` (GEN-005 D-2: filtering moved post-retrieve).

The SQL no longer filters by access_tier (D-2: vision §41 post-retrieval ACL).
ACL filtering is now done in services/acl.py after the retrieve call.
"""

from __future__ import annotations

from collections.abc import Sequence

import asyncpg
import structlog

from archiviste_workers.retrieve.schemas import (
    SQL_TIMEOUT_SECONDS,
    RetrievedChunk,
)

_logger = structlog.get_logger()

_SEARCH_SQL = """
SELECT c.id::text         AS chunk_id,
       d.id::text         AS document_id,
       d.source_path      AS source_path,
       c.ord              AS ord,
       c.text             AS text,
       ROUND((1 - (c.embedding <=> $1))::numeric, 6)::float8 AS score,
       d.access_tier      AS access_tier
  FROM chunks c
  JOIN documents d ON d.id = c.document_id
 ORDER BY c.embedding <=> $1 ASC, c.id ASC
 LIMIT $2
"""


class DatabaseUnavailableError(RuntimeError):
    """Raised when the cosine search cannot complete due to a transport-level failure.

    Wraps `asyncpg.PostgresError`, `asyncpg.InterfaceError`, `OSError`, and
    `TimeoutError`. The original cause is intentionally NOT propagated to the HTTP
    layer (AC-14: no DB error string in the response body).
    """


async def search(
    pool: asyncpg.Pool,
    embedding: Sequence[float],
    allowed_tiers: Sequence[str],  # D-2: ignored post GEN-005, filtering moved to services/acl.py
    top_k: int,
) -> list[RetrievedChunk]:
    """Run the cosine top-K SQL — all tiers returned (D-2, GEN-005 AC-2)."""
    vector = list(embedding)
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(_SEARCH_SQL, vector, top_k, timeout=SQL_TIMEOUT_SECONDS)
    except (
        asyncpg.PostgresError,
        asyncpg.InterfaceError,
        OSError,
        TimeoutError,
    ) as exc:
        _logger.error(
            "search.db_unavailable",
            exc_type=type(exc).__name__,
            exc_repr=repr(exc),
        )
        raise DatabaseUnavailableError from exc
    return [
        RetrievedChunk(
            chunk_id=row["chunk_id"],
            document_id=row["document_id"],
            source_path=row["source_path"],
            ord=row["ord"],
            text=row["text"],
            score=row["score"],
            access_tier=row["access_tier"],
        )
        for row in rows
    ]
