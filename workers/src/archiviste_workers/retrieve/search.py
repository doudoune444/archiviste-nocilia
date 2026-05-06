"""ACL-filtered cosine top-K SQL against `chunks` (RET-001 AC-7/8/10/14)."""

from __future__ import annotations

from collections.abc import Sequence

import asyncpg

from archiviste_workers.retrieve.schemas import (
    SQL_TIMEOUT_SECONDS,
    RetrievedChunk,
)

_SEARCH_SQL = """
SELECT c.id::text         AS chunk_id,
       d.id::text         AS document_id,
       d.source_path      AS source_path,
       c.ord              AS ord,
       c.text             AS text,
       ROUND((1 - (c.embedding <=> $1))::numeric, 6)::float8 AS score
  FROM chunks c
  JOIN documents d ON d.id = c.document_id
 WHERE d.access_tier = ANY($2::text[])
 ORDER BY c.embedding <=> $1 ASC, c.id ASC
 LIMIT $3
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
    allowed_tiers: Sequence[str],
    top_k: int,
) -> list[RetrievedChunk]:
    """Run the ACL-filtered cosine top-K SQL once. AC-7/8/10/11/14."""
    vector = list(embedding)
    tiers = list(allowed_tiers)
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(_SEARCH_SQL, vector, tiers, top_k, timeout=SQL_TIMEOUT_SECONDS)
    except (
        asyncpg.PostgresError,
        asyncpg.InterfaceError,
        OSError,
        TimeoutError,
    ) as exc:
        raise DatabaseUnavailableError from exc
    return [
        RetrievedChunk(
            chunk_id=row["chunk_id"],
            document_id=row["document_id"],
            source_path=row["source_path"],
            ord=row["ord"],
            text=row["text"],
            score=row["score"],
        )
        for row in rows
    ]
