"""query_log INSERT repository (AC-15). Failures non-fatal; surface ALERT log."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import asyncpg
import structlog

logger = structlog.get_logger()


@dataclass(frozen=True)
class QueryLogRow:
    request_id: str
    user_id: str
    conversation_id: str | None
    query_text: str
    mode: str | None
    intent: str | None
    status_code: int
    latency_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None
    cost_eur: Decimal | None


class QueryLogRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def insert(self, row: QueryLogRow) -> bool:
        sql = """
        INSERT INTO query_log (
            request_id, user_id, conversation_id, query_text, intent,
            mode, status_code, latency_ms, prompt_tokens, completion_tokens, cost_eur
        ) VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5,
                  $6, $7, $8, $9, $10, $11)
        """
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    sql,
                    row.request_id,
                    row.user_id,
                    row.conversation_id,
                    row.query_text,
                    row.intent,
                    row.mode,
                    row.status_code,
                    row.latency_ms,
                    row.prompt_tokens,
                    row.completion_tokens,
                    row.cost_eur,
                )
        except (asyncpg.PostgresError, OSError, TimeoutError) as exc:
            logger.error("query_log_failed", request_id=row.request_id, error=str(exc))
            return False
        return True
