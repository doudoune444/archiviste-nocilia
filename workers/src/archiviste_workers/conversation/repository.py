"""Postgres index access for the conversation logger (ING-003)."""

from __future__ import annotations

from datetime import datetime

import asyncpg

from archiviste_workers.conversation.models import UnknownUserError

_INSERT_OR_GET_SQL = """
WITH inserted AS (
    INSERT INTO conversations (id, user_id, gcs_uri)
    VALUES ($1::uuid, $2::uuid, $3)
    ON CONFLICT (id) DO NOTHING
    RETURNING id, created_at, TRUE AS is_new
)
SELECT id, created_at, is_new FROM inserted
UNION ALL
SELECT id, created_at, FALSE AS is_new FROM conversations WHERE id = $1::uuid
LIMIT 1
"""

_INCREMENT_SQL = (
    "UPDATE conversations "
    "SET message_count = message_count + 1, updated_at = NOW() "
    "WHERE id = $1::uuid "
    "RETURNING message_count"
)


class ConversationRepository:
    """asyncpg-backed access to the `conversations` index table."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create_if_absent(
        self, *, conversation_id: str, user_id: str, gcs_uri: str
    ) -> tuple[bool, datetime]:
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(_INSERT_OR_GET_SQL, conversation_id, user_id, gcs_uri)
        except asyncpg.ForeignKeyViolationError as exc:
            raise UnknownUserError from exc
        if row is None:  # pragma: no cover - defensive: SELECT branch always returns.
            raise UnknownUserError
        return bool(row["is_new"]), row["created_at"]

    async def increment_message_count(self, conversation_id: str) -> int:
        async with self._pool.acquire() as conn:
            new_count = await conn.fetchval(_INCREMENT_SQL, conversation_id)
        return int(new_count)
