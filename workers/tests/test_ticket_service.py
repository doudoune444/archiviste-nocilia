"""GEN-004a unit tests - ticket_service.create_or_increment.

Covers AC-10 (a-d: create/increment/cosine-borderline/status-filter),
AC-10 (e): constants byte-for-byte,
AC-15 (f): embedder raises -> skipped_error + log ALERT,
AC-16 (g): DB raises -> skipped_error + log ALERT.
Postgres real (via db_pool fixture); skip if unavailable.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import asyncpg
import pytest
from structlog.testing import capture_logs

from archiviste_workers.embedder import FakeEmbedder
from archiviste_workers.services.ticket_service import (
    TICKET_DEDUP_COSINE_THRESHOLD,
    TICKET_SQL_TIMEOUT_SECONDS,
    create_or_increment,
)

# Shared deterministic embedder — same text always yields the same unit vector.
_embedder = FakeEmbedder()

CONV_ID = "11111111-1111-4111-8111-111111111111"
CONV_ID_2 = "22222222-2222-4222-8222-222222222222"
REQUEST_ID = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"


async def _ensure_conversation(conn: asyncpg.Connection, conv_id: str) -> None:
    """Insert a conversation row if not present (FK required by tickets)."""
    await conn.execute(
        "INSERT INTO conversations (id, user_id, gcs_uri) VALUES ($1, $2, $3) "
        "ON CONFLICT DO NOTHING",
        conv_id,
        "00000000-0000-0000-0000-000000000000",
        f"gs://test/{conv_id}.md",
    )


async def _clean_tickets(conn: asyncpg.Connection) -> None:
    await conn.execute("DELETE FROM tickets")


def test_constants_byte_for_byte() -> None:
    # AC-10 (e): thresholds are exact values specified by spec.
    assert TICKET_DEDUP_COSINE_THRESHOLD == 0.85
    assert TICKET_SQL_TIMEOUT_SECONDS == 5.0


@pytest.mark.asyncio
async def test_create_new_ticket(db_pool: asyncpg.Pool) -> None:
    # AC-10 (a): empty table → creates ticket, priority_score=1, embedding non-null.
    request_id = str(uuid.uuid4())
    async with db_pool.acquire() as conn:
        await _ensure_conversation(conn, CONV_ID)
        await _clean_tickets(conn)

    result = await create_or_increment(
        db_pool,
        _embedder,
        conversation_id=CONV_ID,
        question="Qui est l'Archiviste de Nocilia?",
        request_id=request_id,
    )

    assert result.action == "created"
    assert result.ticket_id is not None
    assert result.priority_score == 1

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT priority_score, status, question_embedding FROM tickets WHERE id=$1",
            result.ticket_id,
        )
    assert row is not None
    assert row["priority_score"] == 1
    assert row["status"] == "open"
    assert row["question_embedding"] is not None

    async with db_pool.acquire() as conn:
        await _clean_tickets(conn)


@pytest.mark.asyncio
async def test_increment_on_cosine_above_threshold(db_pool: asyncpg.Pool) -> None:
    # AC-10 (b): existing open ticket with cosine ≥ 0.85 → incremented, 1 row.
    request_id = str(uuid.uuid4())
    question = "Où se trouve le scriptorium de Nocilia?"
    # FakeEmbedder is deterministic: same text → same vector → cosine = 1.0 (identical).
    async with db_pool.acquire() as conn:
        await _ensure_conversation(conn, CONV_ID)
        await _clean_tickets(conn)

    # Create first ticket.
    first = await create_or_increment(
        db_pool,
        _embedder,
        conversation_id=CONV_ID,
        question=question,
        request_id=str(uuid.uuid4()),
    )
    assert first.action == "created"
    assert first.priority_score == 1

    # Same question → cosine = 1.0 ≥ 0.85 → increment.
    second = await create_or_increment(
        db_pool,
        _embedder,
        conversation_id=CONV_ID,
        question=question,
        request_id=request_id,
    )

    assert second.action == "incremented"
    assert second.ticket_id == first.ticket_id
    assert second.priority_score == 2

    async with db_pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM tickets")
        assert count == 1

    async with db_pool.acquire() as conn:
        await _clean_tickets(conn)


@pytest.mark.asyncio
async def test_create_distinct_on_cosine_below_threshold(db_pool: asyncpg.Pool) -> None:
    # AC-10 (c): distinct text → FakeEmbedder yields different vector → cosine < 0.85 → create.
    # We use two completely unrelated questions to guarantee low cosine similarity.
    async with db_pool.acquire() as conn:
        await _ensure_conversation(conn, CONV_ID)
        await _clean_tickets(conn)

    first = await create_or_increment(
        db_pool,
        _embedder,
        conversation_id=CONV_ID,
        question="Qui est l'Archiviste?",
        request_id=str(uuid.uuid4()),
    )
    assert first.action == "created"

    second = await create_or_increment(
        db_pool,
        _embedder,
        conversation_id=CONV_ID,
        question="Quelles sont les lois du commerce dans l'Empire de Vorn?",
        request_id=str(uuid.uuid4()),
    )

    # FakeEmbedder is deterministic per-text but different texts produce different
    # random unit vectors — cosine similarity is almost certainly < 0.85.
    # If both land above threshold by chance, the test would fail; that is the correct
    # signal (embedder not truly distinct). In practice SHA-256 RNG gives near-orthogonal
    # 1024-dim vectors.
    assert second.action == "created"
    assert second.ticket_id != first.ticket_id

    async with db_pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM tickets")
        assert count == 2

    async with db_pool.acquire() as conn:
        await _clean_tickets(conn)


@pytest.mark.asyncio
async def test_resolved_ticket_not_incremented(db_pool: asyncpg.Pool) -> None:
    # AC-10 (d): existing ticket cosine ≥ 0.85 but status='resolved' → SELECT filters it → create.
    question = "Quel est le rôle du gardien des archives?"
    async with db_pool.acquire() as conn:
        await _ensure_conversation(conn, CONV_ID)
        await _clean_tickets(conn)

    first = await create_or_increment(
        db_pool,
        _embedder,
        conversation_id=CONV_ID,
        question=question,
        request_id=str(uuid.uuid4()),
    )
    assert first.action == "created"

    # Manually mark the ticket as resolved.
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE tickets SET status='resolved' WHERE id=$1", first.ticket_id)

    second = await create_or_increment(
        db_pool,
        _embedder,
        conversation_id=CONV_ID,
        question=question,
        request_id=str(uuid.uuid4()),
    )

    assert second.action == "created"
    assert second.ticket_id != first.ticket_id

    async with db_pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM tickets")
        assert count == 2

    async with db_pool.acquire() as conn:
        await _clean_tickets(conn)


@pytest.mark.asyncio
async def test_embed_failure_returns_skipped_error(db_pool: asyncpg.Pool) -> None:
    # AC-15 (f): encode_batch raises -> skipped_error + log ALERT reason=embed_failed.
    class _BrokenEmbedder:
        def encode_batch(self, texts: list[str], batch_size: int) -> list[list[float]]:
            raise RuntimeError("oom")

    async with db_pool.acquire() as conn:
        await _ensure_conversation(conn, CONV_ID)
        await _clean_tickets(conn)

    with capture_logs() as logs:
        result = await create_or_increment(
            db_pool,
            _BrokenEmbedder(),
            conversation_id=CONV_ID,
            question="Qui gouverne Nocilia?",
            request_id=REQUEST_ID,
        )

    assert result.action == "skipped_error"
    assert result.ticket_id is None

    alert_logs = [lg for lg in logs if lg.get("event") == "ticket_service_failed"]
    assert len(alert_logs) == 1
    assert alert_logs[0]["reason"] == "embed_failed"
    assert alert_logs[0]["request_id"] == REQUEST_ID

    async with db_pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM tickets")
        assert count == 0

    async with db_pool.acquire() as conn:
        await _clean_tickets(conn)


@pytest.mark.asyncio
async def test_judges_not_passed_set_on_insert(db_pool: asyncpg.Pool) -> None:
    # #163 AC: judges_not_passed=True is written on INSERT, readable from the DB.
    question = "Qui surveille les archives de nuit?"
    async with db_pool.acquire() as conn:
        await _ensure_conversation(conn, CONV_ID)
        await _clean_tickets(conn)

    result = await create_or_increment(
        db_pool,
        _embedder,
        conversation_id=CONV_ID,
        question=question,
        request_id=str(uuid.uuid4()),
        judges_not_passed=True,
    )

    assert result.action == "created"
    assert result.ticket_id is not None

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT judges_not_passed FROM tickets WHERE id=$1",
            result.ticket_id,
        )
    assert row is not None
    assert row["judges_not_passed"] is True

    async with db_pool.acquire() as conn:
        await _clean_tickets(conn)


@pytest.mark.asyncio
async def test_non_force_cosine_match_increments_existing_ticket(db_pool: asyncpg.Pool) -> None:
    # #175 AC (non-force path unchanged): cosine match without force=True → increment, 1 row.
    question = "Quel est le nom du gardien des archives de nuit?"
    async with db_pool.acquire() as conn:
        await _ensure_conversation(conn, CONV_ID)
        await _clean_tickets(conn)

    # First: judge-confirmed insert (judges_not_passed=False).
    first = await create_or_increment(
        db_pool,
        _embedder,
        conversation_id=CONV_ID,
        question=question,
        request_id=str(uuid.uuid4()),
        judges_not_passed=False,
    )
    assert first.action == "created"

    # Second: non-force with same question → cosine match → increment.
    second = await create_or_increment(
        db_pool,
        _embedder,
        conversation_id=CONV_ID,
        question=question,
        request_id=str(uuid.uuid4()),
        judges_not_passed=True,
    )
    assert second.action == "incremented"
    assert second.ticket_id == first.ticket_id

    async with db_pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM tickets")
        assert count == 1

    async with db_pool.acquire() as conn:
        await _clean_tickets(conn)


@pytest.mark.asyncio
async def test_force_always_inserts_new_ticket_bypassing_dedup(db_pool: asyncpg.Pool) -> None:
    # #175 AC: force=True → always INSERT new row with judges_not_passed=True, never increment.
    # AC-1: a force signal always inserts a new ticket with judges_not_passed=True.
    # AC-2: a force signal does not merge into / increment an existing ticket.
    question = "Quel est le nom du gardien des archives de nuit?"
    async with db_pool.acquire() as conn:
        await _ensure_conversation(conn, CONV_ID)
        await _clean_tickets(conn)

    # First: judge-confirmed insert (judges_not_passed=False), open status.
    first = await create_or_increment(
        db_pool,
        _embedder,
        conversation_id=CONV_ID,
        question=question,
        request_id=str(uuid.uuid4()),
        judges_not_passed=False,
    )
    assert first.action == "created"

    # Second: force=True + same question + similar open ticket exists → must INSERT new row.
    second = await create_or_increment(
        db_pool,
        _embedder,
        conversation_id=CONV_ID,
        question=question,
        request_id=str(uuid.uuid4()),
        judges_not_passed=True,
        force=True,
    )

    # AC-1: new ticket created, not incremented.
    assert second.action == "created"
    # AC-2: separate ticket (different id from the first one).
    assert second.ticket_id != first.ticket_id
    assert second.priority_score == 1

    # Two rows in DB — one per signal.
    async with db_pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM tickets")
        assert count == 2

    # New ticket carries judges_not_passed=True.
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT judges_not_passed FROM tickets WHERE id=$1",
            second.ticket_id,
        )
    assert row is not None
    assert row["judges_not_passed"] is True

    # Original ticket is untouched (judges_not_passed still False, priority_score still 1).
    async with db_pool.acquire() as conn:
        orig = await conn.fetchrow(
            "SELECT judges_not_passed, priority_score FROM tickets WHERE id=$1",
            first.ticket_id,
        )
    assert orig is not None
    assert orig["judges_not_passed"] is False
    assert orig["priority_score"] == 1

    async with db_pool.acquire() as conn:
        await _clean_tickets(conn)


@pytest.mark.asyncio
async def test_db_failure_returns_skipped_error(db_pool: asyncpg.Pool) -> None:
    # AC-16 (g): pool.acquire raises PostgresError → skipped_error + log ALERT reason=db_failed.
    async with db_pool.acquire() as conn:
        await _ensure_conversation(conn, CONV_ID)
        await _clean_tickets(conn)

    with patch.object(db_pool, "acquire") as mock_acquire:
        mock_acquire.side_effect = asyncpg.PostgresError("connection refused")

        with capture_logs() as logs:
            result = await create_or_increment(
                db_pool,
                _embedder,
                conversation_id=CONV_ID,
                question="Qui gouverne Nocilia?",
                request_id=REQUEST_ID,
            )

    assert result.action == "skipped_error"
    assert result.ticket_id is None

    alert_logs = [lg for lg in logs if lg.get("event") == "ticket_service_failed"]
    assert len(alert_logs) == 1
    assert alert_logs[0]["reason"] == "db_failed"
    assert alert_logs[0]["request_id"] == REQUEST_ID

    async with db_pool.acquire() as conn:
        await _clean_tickets(conn)
