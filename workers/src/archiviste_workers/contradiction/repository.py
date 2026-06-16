"""ACL-bounded re-resolution of cited chunk text from the trusted store (CTR-001).

Client-supplied citations are references only; the source text the judges see is
read here from the `chunks` table, ACL-filtered by caller tier. This blocks a
visitor from forging source bodies to force a confirmation (security.md A01 / RAG).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

import asyncpg
import structlog

from archiviste_workers.contradiction.models import Citation
from archiviste_workers.services.acl import ALLOWED_ACCESS_TIERS_BY_USER_TIER

logger = structlog.get_logger()

RESOLVE_SQL_TIMEOUT_SECONDS: Final = 5.0

_RESOLVE_SQL: Final = """
SELECT d.source_path AS source_path, c.ord AS ord, c.text AS text
  FROM chunks c
  JOIN documents d ON d.id = c.document_id
 WHERE d.source_path = ANY($1)
   AND d.access_tier = ANY($2)
 ORDER BY d.source_path, c.ord
"""


class SourceResolutionError(RuntimeError):
    """Transport-level DB failure during cited-chunk resolution (no cause propagated)."""


async def resolve_cited_sources(
    pool: asyncpg.Pool, citations: Sequence[Citation], user_tier: str
) -> list[tuple[str, int, str]]:
    """Return (source_path, ord, text) for cited chunks visible to user_tier.

    Fail-closed: chunks above the caller's tier or unknown tiers are dropped. Only
    the exact (source_path, ord) pairs the client cited are returned.
    """
    allowed = list(ALLOWED_ACCESS_TIERS_BY_USER_TIER.get(user_tier, frozenset()))
    wanted = {
        (citation.source_path, ord_) for citation in citations for ord_ in citation.chunk_ords
    }
    if not allowed or not wanted:
        return []
    source_paths = list({citation.source_path for citation in citations})
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                _RESOLVE_SQL, source_paths, allowed, timeout=RESOLVE_SQL_TIMEOUT_SECONDS
            )
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, TimeoutError) as exc:
        logger.error("contradiction_resolve_db_unavailable", exc_type=type(exc).__name__)
        raise SourceResolutionError from exc
    return [
        (row["source_path"], row["ord"], row["text"])
        for row in rows
        if (row["source_path"], row["ord"]) in wanted
    ]
