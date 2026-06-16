"""Integration tests for MEM-001: conversation_messages double-write.

Requires Postgres + fake-gcs-server. Run via:

    docker compose --profile tools up -d gcs postgres
    GCS_EMULATOR_HOST=http://localhost:4443 \\
    DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/archiviste \\
    GCS_BUCKET=archiviste-conversations-test \\
    uv run pytest workers/tests/test_mem001_conversation_messages_integration.py -m integration
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import asyncpg
import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport

pytestmark = pytest.mark.integration

EMULATOR = os.environ.get("GCS_EMULATOR_HOST")
DSN = os.environ.get("DATABASE_URL")
BUCKET = os.environ.get("GCS_BUCKET", "archiviste-conversations-test")

if not EMULATOR or not DSN:
    pytest.skip(
        "GCS_EMULATOR_HOST and DATABASE_URL required for MEM-001 integration suite",
        allow_module_level=True,
    )

from archiviste_workers.conversation import repository as repo_module  # noqa: E402
from archiviste_workers.conversation.repository import ConversationRepository  # noqa: E402
from archiviste_workers.db import create_pool  # noqa: E402
from archiviste_workers.main import app  # noqa: E402

USER_ID = "00000000-0000-0000-0000-000000000000"


@pytest_asyncio.fixture
async def app_client() -> AsyncIterator[httpx.AsyncClient]:
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient() as raw:
            await raw.post(
                f"{EMULATOR}/storage/v1/b",
                json={"name": BUCKET},
                params={"project": "archiviste-test"},
            )
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


def _payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "role": "user",
        "content": "hello world",
        "timestamp": datetime.now(UTC).isoformat(),
        "user_id": USER_ID,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_double_write_inserts_message_row(app_client: httpx.AsyncClient) -> None:
    """AC (MEM-001): after a successful POST, one row exists in conversation_messages
    with correct role, ordinal=0, content, and a positive token_count.
    """
    cid = str(uuid.uuid4())
    resp = await app_client.post(
        f"/v1/conversations/{cid}/messages", json=_payload(content="first message")
    )
    assert resp.status_code == 201

    assert DSN is not None
    conn = await asyncpg.connect(DSN.replace("+asyncpg", ""))
    try:
        row = await conn.fetchrow(
            "SELECT role, ordinal, content, token_count "
            "FROM conversation_messages WHERE conversation_id = $1::uuid",
            cid,
        )
    finally:
        await conn.close()

    assert row is not None
    assert row["role"] == "user"
    assert row["ordinal"] == 0
    assert row["content"] == "first message"
    assert row["token_count"] > 0


@pytest.mark.asyncio
async def test_tail_read_returns_newest_first(app_client: httpx.AsyncClient) -> None:
    """AC (MEM-001): fetch_tail returns turns ordered newest-first (DESC ordinal)."""
    cid = str(uuid.uuid4())
    for content in ("turn-0", "turn-1", "turn-2"):
        resp = await app_client.post(
            f"/v1/conversations/{cid}/messages", json=_payload(content=content)
        )
        assert resp.status_code == 201

    assert DSN is not None
    pool = await create_pool(DSN)
    try:
        repo = ConversationRepository(pool)
        tail = await repo.fetch_tail(cid, limit=10)
    finally:
        await pool.close()

    assert len(tail) == 3
    assert [row.ordinal for row in tail] == [2, 1, 0]
    assert tail[0].content == "turn-2"
    assert tail[2].content == "turn-0"


@pytest.mark.asyncio
async def test_gcs_source_of_truth_preserved_on_db_failure(
    app_client: httpx.AsyncClient,
) -> None:
    """AC (MEM-001 / ING-003): a forced structured-store insert failure still returns 201
    and GCS is written (source-of-truth invariant at integration level).
    """
    call_count = 0

    async def _failing_insert(self: Any, **kwargs: Any) -> None:
        nonlocal call_count
        call_count += 1
        raise asyncpg.PostgresError("injected failure")

    cid = str(uuid.uuid4())
    with patch.object(repo_module.ConversationRepository, "insert_message", _failing_insert):
        resp = await app_client.post(
            f"/v1/conversations/{cid}/messages", json=_payload(content="patched")
        )

    assert resp.status_code == 201
    assert call_count == 1

    async with httpx.AsyncClient() as raw:
        gcs_resp = await raw.get(
            f"{EMULATOR}/storage/v1/b/{BUCKET}/o/{cid}.md", params={"alt": "media"}
        )
    assert gcs_resp.status_code == 200
    assert "patched" in gcs_resp.text
