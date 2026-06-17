"""Postgres index access for the conversation logger (ING-003 / MEM-001)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import asyncpg

from archiviste_workers.conversation.models import Role, UnknownUserError

_INSERT_OR_GET_SQL = """
WITH inserted AS (
    INSERT INTO conversations (id, user_id, gcs_uri, created_at)
    VALUES ($1::uuid, $2::uuid, $3, $4)
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

# Anonymous callers are identified by a fingerprint-derived UUIDv5 (gateway
# SEC-001) that has no pre-existing `users` row. Persist it on first use so the
# `conversations.user_id` FK is satisfiable and anonymous histories are kept.
# Members/authors already have a row → ON CONFLICT no-ops, tier untouched.
_UPSERT_ANON_USER_SQL = (
    "INSERT INTO users (id, tier) VALUES ($1::uuid, 'anonymous') ON CONFLICT (id) DO NOTHING"
)

# MEM-001: next ordinal = current message_count before the increment, which
# equals the zero-based index of the turn being inserted.
_INSERT_MESSAGE_SQL = """
INSERT INTO conversation_messages
    (conversation_id, role, ordinal, content, token_count)
VALUES ($1::uuid, $2, $3, $4, $5)
"""

# MEM-001: bounded tail read, newest-first by ordinal.
# The index conversation_messages_tail_idx (conversation_id, ordinal DESC)
# makes this an index-only scan for reasonable limits.
_TAIL_SQL = """
SELECT role, ordinal, content, token_count, created_at
FROM conversation_messages
WHERE conversation_id = $1::uuid
ORDER BY ordinal DESC
LIMIT $2
"""

# MEM-002: owner-scoped tail read. The JOIN to conversations enforces ownership
# in SQL so a caller passing someone else's conversation_id gets zero rows (no
# cross-conversation history leak — security.md A01 IDOR). conversation_messages
# has no user_id of its own; ownership lives on the parent conversations row.
_TAIL_OWNED_SQL = """
SELECT cm.role, cm.ordinal, cm.content, cm.token_count, cm.created_at
FROM conversation_messages cm
JOIN conversations c ON c.id = cm.conversation_id
WHERE cm.conversation_id = $1::uuid AND c.user_id = $2::uuid
ORDER BY cm.ordinal DESC
LIMIT $3
"""


@dataclass(frozen=True)
class MessageRow:
    """One row from conversation_messages returned by fetch_tail."""

    role: Role
    ordinal: int
    content: str
    token_count: int
    created_at: datetime


def _to_message_row(row: asyncpg.Record) -> MessageRow:
    return MessageRow(
        role=row["role"],
        ordinal=row["ordinal"],
        content=row["content"],
        token_count=row["token_count"],
        created_at=row["created_at"],
    )


class ConversationRepository:
    """asyncpg-backed access to the `conversations` index table."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create_if_absent(
        self, *, conversation_id: str, user_id: str, gcs_uri: str, created_at: datetime
    ) -> tuple[bool, datetime]:
        """Insert a new conversations row or return the existing one.

        For a new row, `created_at` is stored as the conversation's genesis
        timestamp (= first message timestamp) rather than DB NOW(), eliminating
        the app-clock vs DB-clock race that caused false 422s on turn 1.
        For an existing row (ON CONFLICT DO NOTHING), `created_at` is ignored
        and the stored value is returned unchanged.
        """
        try:
            async with self._pool.acquire() as conn, conn.transaction():
                await conn.execute(_UPSERT_ANON_USER_SQL, user_id)
                row = await conn.fetchrow(
                    _INSERT_OR_GET_SQL, conversation_id, user_id, gcs_uri, created_at
                )
        except asyncpg.ForeignKeyViolationError as exc:
            raise UnknownUserError from exc
        if row is None:  # pragma: no cover - defensive: SELECT branch always returns.
            raise UnknownUserError
        return bool(row["is_new"]), row["created_at"]

    async def increment_message_count(self, conversation_id: str) -> int:
        async with self._pool.acquire() as conn:
            new_count = await conn.fetchval(_INCREMENT_SQL, conversation_id)
        return int(new_count)

    async def insert_message(
        self,
        *,
        conversation_id: str,
        role: Role,
        ordinal: int,
        content: str,
        token_count: int,
    ) -> None:
        """Insert one turn into conversation_messages (best-effort; caller handles errors)."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                _INSERT_MESSAGE_SQL,
                conversation_id,
                role,
                ordinal,
                content,
                token_count,
            )

    async def fetch_tail(self, conversation_id: str, *, limit: int) -> list[MessageRow]:
        """Return up to *limit* most recent turns, newest-first by ordinal (MEM-001)."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_TAIL_SQL, conversation_id, limit)
        return [_to_message_row(row) for row in rows]

    async def fetch_tail_owned(
        self, conversation_id: str, user_id: str, *, limit: int
    ) -> list[MessageRow]:
        """Owner-scoped tail read (MEM-002): zero rows if *user_id* is not the owner.

        Closes the cross-conversation history-leak IDOR: ownership is enforced in
        SQL (JOIN on conversations.user_id), so the caller cannot read turns from a
        conversation_id they do not own.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_TAIL_OWNED_SQL, conversation_id, user_id, limit)
        return [_to_message_row(row) for row in rows]
