"""Integration tests for the conversation logger (ING-003).

Marked `integration`: requires fake-gcs-server + Postgres. Run via:

    docker compose --profile tools up -d gcs postgres
    GCS_EMULATOR_HOST=http://localhost:4443 \
    DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/archiviste \
    GCS_BUCKET=archiviste-conversations-test \
    uv run pytest workers/tests/test_conversation_integration.py -m integration
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import asyncpg
import httpx
import pytest
import pytest_asyncio
import structlog
from httpx import ASGITransport

pytestmark = pytest.mark.integration

EMULATOR = os.environ.get("GCS_EMULATOR_HOST")
DSN = os.environ.get("DATABASE_URL")
BUCKET = os.environ.get("GCS_BUCKET", "archiviste-conversations-test")

if not EMULATOR or not DSN:
    pytest.skip(
        "GCS_EMULATOR_HOST and DATABASE_URL required for integration suite",
        allow_module_level=True,
    )

from archiviste_workers.main import app  # noqa: E402

USER_ID = "00000000-0000-0000-0000-000000000000"


@pytest_asyncio.fixture
async def app_client() -> AsyncIterator[httpx.AsyncClient]:
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        # Best-effort: ensure the bucket exists on the emulator before tests.
        async with httpx.AsyncClient() as raw:
            await raw.post(
                f"{EMULATOR}/storage/v1/b",
                json={"name": BUCKET},
                params={"project": "archiviste-test"},
            )
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


def _payload(**overrides: Any) -> dict[str, Any]:
    base = {
        "role": "user",
        "content": "hello world",
        "timestamp": datetime.now(UTC).isoformat(),
        "user_id": USER_ID,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_post_message_returns_201_with_payload(app_client: httpx.AsyncClient) -> None:
    """AC-1 / AC-2: first message creates row + GCS object, returns 201."""
    cid = str(uuid.uuid4())
    response = await app_client.post(f"/v1/conversations/{cid}/messages", json=_payload())
    assert response.status_code == 201
    body = response.json()
    assert body["conversation_id"] == cid
    assert body["gcs_uri"] == f"gs://{BUCKET}/{cid}.md"
    assert body["generation"] > 0
    assert body["message_count"] >= 1


@pytest.mark.asyncio
async def test_append_appends_block_with_exact_format(app_client: httpx.AsyncClient) -> None:
    """AC-3: subsequent append concatenates the exact block format."""
    cid = str(uuid.uuid4())
    await app_client.post(f"/v1/conversations/{cid}/messages", json=_payload(content="first"))
    second = await app_client.post(
        f"/v1/conversations/{cid}/messages",
        json=_payload(role="assistant", content="second"),
    )
    assert second.status_code == 201
    async with httpx.AsyncClient() as raw:
        obj = await raw.get(f"{EMULATOR}/storage/v1/b/{BUCKET}/o/{cid}.md", params={"alt": "media"})
    text = obj.text
    assert text.startswith(f"# Conversation {cid}\n")
    assert "## [" in text
    assert "user\nfirst\n\n" in text
    assert "assistant\nsecond\n\n" in text


@pytest.mark.asyncio
async def test_log_event_excludes_content(app_client: httpx.AsyncClient) -> None:
    """AC-11: structured log emits required fields, never content."""
    cid = str(uuid.uuid4())
    secret = "TOPSECRETPAYLOAD"
    with structlog.testing.capture_logs() as logs:
        await app_client.post(f"/v1/conversations/{cid}/messages", json=_payload(content=secret))
    events = [entry for entry in logs if entry.get("event") == "conversation_message"]
    assert events, "expected one conversation_message log entry"
    entry = events[-1]
    assert entry["conversation_id"] == cid
    assert entry["role"] == "user"
    assert entry["bytes"] == len(secret.encode("utf-8"))
    assert "content" not in entry
    assert all(secret not in str(value) for value in entry.values())


@pytest.mark.asyncio
async def test_concurrent_appends_resolve(app_client: httpx.AsyncClient) -> None:
    """AC-15: two concurrent appends -> at least one 201, no orphan 5xx."""
    cid = str(uuid.uuid4())
    await app_client.post(f"/v1/conversations/{cid}/messages", json=_payload(content="seed"))
    a, b = await asyncio.gather(
        app_client.post(f"/v1/conversations/{cid}/messages", json=_payload(content="A")),
        app_client.post(f"/v1/conversations/{cid}/messages", json=_payload(content="B")),
    )
    statuses = sorted([a.status_code, b.status_code])
    assert statuses[0] == 201
    assert statuses[1] in (201, 409)


@pytest.mark.asyncio
async def test_anonymous_user_is_persisted_on_first_use(app_client: httpx.AsyncClient) -> None:
    """Anonymous fingerprint user_id (no pre-seeded `users` row) is upserted, so the
    conversation persists instead of failing the FK with 422 unknown_user.

    Guards the fix for the GET /v1/stats counter staying at 0: anonymous chats must
    produce a `conversations` row so count(*) reflects real usage.
    """
    anon_user_id = str(uuid.uuid4())
    cid = str(uuid.uuid4())

    response = await app_client.post(
        f"/v1/conversations/{cid}/messages",
        json=_payload(user_id=anon_user_id),
    )
    assert response.status_code == 201

    conn = await asyncpg.connect(DSN.replace("+asyncpg", ""))
    try:
        user_tier = await conn.fetchval("SELECT tier FROM users WHERE id = $1::uuid", anon_user_id)
        convo_owner = await conn.fetchval(
            "SELECT user_id FROM conversations WHERE id = $1::uuid", cid
        )
    finally:
        await conn.close()

    assert user_tier == "anonymous"
    assert str(convo_owner) == anon_user_id
