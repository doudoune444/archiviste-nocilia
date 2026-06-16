"""Unit tests for MEM-001: conversation_messages double-write (no external services).

Integration tests requiring Postgres + GCS are in
``test_mem001_conversation_messages_integration.py``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import asyncpg
import httpx
import pytest

from archiviste_workers.conversation.token_counter import count_tokens

# ---------------------------------------------------------------------------
# Token counter -- pure unit tests (no I/O)
# ---------------------------------------------------------------------------


def test_count_tokens_is_deterministic() -> None:
    """AC (MEM-001): token_count is a reproducible deterministic count."""
    text = "Hello Nocilia, what is the lore of the Archiviste?"
    assert count_tokens(text) == count_tokens(text)
    assert count_tokens(text) > 0


def test_count_tokens_non_negative() -> None:
    """Token count for any input is non-negative (tokenizer may emit BOS for empty)."""
    assert count_tokens("") >= 0


def test_count_tokens_increases_with_length() -> None:
    """Longer content always yields more tokens than shorter content."""
    short = "Hi"
    long = "Hi " * 100
    assert count_tokens(long) > count_tokens(short)


# ---------------------------------------------------------------------------
# Router double-write -- best-effort semantics (mocked repo + storage)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_structured_insert_failure_is_nonfatal(
    worker_client: httpx.AsyncClient,
    fake_repo: Any,
    fake_storage: Any,
) -> None:
    """AC (MEM-001 / ING-003): a forced insert_message failure is non-fatal; returns 201.

    ING-003 invariant: GCS is source of truth. A DB failure MUST NOT affect
    the 2xx response -- the caller has already appended to GCS.
    """
    created_at = datetime.now(UTC) - timedelta(minutes=1)
    fake_repo.create_if_absent = AsyncMock(return_value=(True, created_at))
    fake_repo.increment_message_count = AsyncMock(return_value=1)
    # insert_message raises a Postgres error -- must not propagate
    fake_repo.insert_message = AsyncMock(
        side_effect=asyncpg.PostgresError("simulated structured-store failure")
    )

    payload = {
        "role": "user",
        "content": "will the insert fail?",
        "timestamp": datetime.now(UTC).isoformat(),
        "user_id": "00000000-0000-0000-0000-000000000000",
    }
    cid = str(uuid.uuid4())
    response = await worker_client.post(f"/v1/conversations/{cid}/messages", json=payload)

    # AC: GCS append + 2xx even when structured store fails
    assert response.status_code == 201
    fake_storage.append_block.assert_awaited_once()
    fake_repo.insert_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_double_write_calls_insert_message_with_token_count(
    worker_client: httpx.AsyncClient,
    fake_repo: Any,
) -> None:
    """AC (MEM-001): on success, insert_message is called with a positive token_count."""
    created_at = datetime.now(UTC) - timedelta(minutes=1)
    fake_repo.create_if_absent = AsyncMock(return_value=(True, created_at))
    fake_repo.increment_message_count = AsyncMock(return_value=1)
    fake_repo.insert_message = AsyncMock(return_value=None)

    content = "What is the Archive of Nocilia?"
    payload = {
        "role": "user",
        "content": content,
        "timestamp": datetime.now(UTC).isoformat(),
        "user_id": "00000000-0000-0000-0000-000000000000",
    }
    cid = str(uuid.uuid4())
    response = await worker_client.post(f"/v1/conversations/{cid}/messages", json=payload)

    assert response.status_code == 201
    fake_repo.insert_message.assert_awaited_once()
    call_kwargs = fake_repo.insert_message.call_args.kwargs
    assert call_kwargs["token_count"] > 0
    assert call_kwargs["ordinal"] == 0  # first message -> ordinal 0
    assert call_kwargs["role"] == "user"
    assert call_kwargs["content"] == content


@pytest.mark.asyncio
async def test_double_write_skipped_when_counter_fails(
    worker_client: httpx.AsyncClient,
    fake_repo: Any,
) -> None:
    """AC (MEM-001): if increment_message_count fails, insert_message is NOT called.

    Without a reliable ordinal we skip the structured insert to avoid
    inconsistent ordinal sequences -- ING-003 invariant preserved (still 201).
    """
    created_at = datetime.now(UTC) - timedelta(minutes=1)
    fake_repo.create_if_absent = AsyncMock(return_value=(False, created_at))
    fake_repo.increment_message_count = AsyncMock(
        side_effect=asyncpg.PostgresError("counter failure")
    )
    fake_repo.insert_message = AsyncMock(return_value=None)

    payload = {
        "role": "user",
        "content": "counter will fail",
        "timestamp": datetime.now(UTC).isoformat(),
        "user_id": "00000000-0000-0000-0000-000000000000",
    }
    cid = str(uuid.uuid4())
    response = await worker_client.post(f"/v1/conversations/{cid}/messages", json=payload)

    # Still 201 -- ING-003 invariant: DB failure is non-fatal
    assert response.status_code == 201
    fake_repo.insert_message.assert_not_awaited()
