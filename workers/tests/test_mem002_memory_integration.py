"""Integration tests for MEM-002: owner-scoped memory tail read (IDOR guard).

Requires Postgres + fake-gcs-server. Run via:

    docker compose --profile tools up -d gcs postgres
    GCS_EMULATOR_HOST=http://localhost:4443 \\
    DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/archiviste \\
    GCS_BUCKET=archiviste-conversations-test \\
    uv run pytest workers/tests/test_mem002_memory_integration.py -m integration
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

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
        "GCS_EMULATOR_HOST and DATABASE_URL required for MEM-002 integration suite",
        allow_module_level=True,
    )

from archiviste_workers.conversation.repository import ConversationRepository  # noqa: E402
from archiviste_workers.db import create_pool  # noqa: E402
from archiviste_workers.main import app  # noqa: E402

OWNER_ID = "00000000-0000-0000-0000-000000000000"
OTHER_ID = "99999999-9999-4999-8999-999999999999"


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


def _message(content: str) -> dict[str, Any]:
    return {
        "role": "user",
        "content": content,
        "timestamp": datetime.now(UTC).isoformat(),
        "user_id": OWNER_ID,
    }


async def _seed_conversation(app_client: httpx.AsyncClient) -> str:
    cid = str(uuid.uuid4())
    resp = await app_client.post(f"/v1/conversations/{cid}/messages", json=_message("owned turn"))
    assert resp.status_code == 201
    return cid


@pytest.mark.asyncio
async def test_owner_reads_own_tail(app_client: httpx.AsyncClient) -> None:
    cid = await _seed_conversation(app_client)
    assert DSN is not None
    pool = await create_pool(DSN)
    try:
        tail = await ConversationRepository(pool).fetch_tail_owned(cid, OWNER_ID, limit=10)
    finally:
        await pool.close()
    assert [row.content for row in tail] == ["owned turn"]


@pytest.mark.asyncio
async def test_non_owner_reads_nothing(app_client: httpx.AsyncClient) -> None:
    # IDOR guard: a different user_id for the same conversation_id gets zero rows.
    cid = await _seed_conversation(app_client)
    assert DSN is not None
    pool = await create_pool(DSN)
    try:
        tail = await ConversationRepository(pool).fetch_tail_owned(cid, OTHER_ID, limit=10)
    finally:
        await pool.close()
    assert tail == []
