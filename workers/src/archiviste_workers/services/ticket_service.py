"""Lore-gap ticket service — create or increment with cosine dedup (GEN-004 AC-10).

Single transaction: embed question -> SELECT FOR UPDATE cosine match -> INSERT or UPDATE.
Failure modes are fail-soft: any exception returns skipped_error + logs ALERT.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal, Protocol

import asyncpg
import structlog

logger = structlog.get_logger()

TICKET_DEDUP_COSINE_THRESHOLD: Final = 0.85
TICKET_SQL_TIMEOUT_SECONDS: Final = 5.0

TicketAction = Literal["created", "incremented", "skipped_error"]


class _EmbedderProtocol(Protocol):
    def encode_batch(self, texts: list[str], batch_size: int) -> list[list[float]]: ...


@dataclass(frozen=True)
class TicketResult:
    action: TicketAction
    ticket_id: str | None
    priority_score: int | None


async def create_or_increment(
    pool: asyncpg.Pool,
    embedder: _EmbedderProtocol,
    *,
    conversation_id: str,
    question: str,
    request_id: str,
    judges_not_passed: bool = False,
) -> TicketResult:
    """Create a new lore-gap ticket or increment an existing similar one.

    Returns TicketResult with action in {"created", "incremented", "skipped_error"}.
    Never raises: all failures are logged as ALERT and return skipped_error.

    judges_not_passed is only written on INSERT (new tickets). Incrementing an
    existing ticket only bumps priority_score — the original confirmation status is
    immutable, as it reflects how the first signal was generated (#163).
    """
    try:
        vector = embedder.encode_batch([question], batch_size=1)[0]
    except Exception as exc:
        logger.error(
            "ticket_service_failed",
            request_id=request_id,
            reason="embed_failed",
            error_type=type(exc).__name__,
        )
        return TicketResult(action="skipped_error", ticket_id=None, priority_score=None)

    try:
        return await _run_transaction(
            pool, vector, conversation_id, question, request_id, judges_not_passed
        )
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, TimeoutError) as exc:
        logger.error(
            "ticket_service_failed",
            request_id=request_id,
            reason="db_failed",
            error_type=type(exc).__name__,
        )
        return TicketResult(action="skipped_error", ticket_id=None, priority_score=None)


async def _run_transaction(
    pool: asyncpg.Pool,
    vector: list[float],
    conversation_id: str,
    question: str,
    request_id: str,
    judges_not_passed: bool,
) -> TicketResult:
    """Execute the dedup SELECT FOR UPDATE + INSERT/UPDATE in a single transaction."""
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            f"SET LOCAL statement_timeout = '{int(TICKET_SQL_TIMEOUT_SECONDS * 1000)}'"
        )

        # AC-10 step 2: find open ticket with cosine similarity >= threshold.
        existing = await conn.fetchrow(
            """
            SELECT id, priority_score
            FROM tickets
            WHERE status = 'open'
              AND 1 - (question_embedding <=> $1) >= $2
            ORDER BY question_embedding <=> $1 ASC
            LIMIT 1
            FOR UPDATE
            """,
            vector,
            TICKET_DEDUP_COSINE_THRESHOLD,
        )

        if existing is not None:
            # AC-10 step 3: increment priority_score.
            # judges_not_passed is NOT updated on increment — the original creation
            # flag reflects the confirmation status at birth and is immutable (#163).
            new_score: int = await conn.fetchval(
                """
                UPDATE tickets
                SET priority_score = priority_score + 1, updated_at = NOW()
                WHERE id = $1
                RETURNING priority_score
                """,
                existing["id"],
            )
            return TicketResult(
                action="incremented",
                ticket_id=str(existing["id"]),
                priority_score=new_score,
            )

        # AC-10 step 4: insert new ticket.
        new_id: str = await conn.fetchval(
            """
            INSERT INTO tickets (conversation_id, question, question_embedding,
                                 category, priority_score, status, judges_not_passed)
            VALUES ($1, $2, $3, 'uncategorized', 1, 'open', $4)
            RETURNING id::text
            """,
            conversation_id,
            question,
            vector,
            judges_not_passed,
        )
        return TicketResult(action="created", ticket_id=new_id, priority_score=1)
