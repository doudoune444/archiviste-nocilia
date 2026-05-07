"""GEN-001 request validation unit tests (AC-17, AC-18, AC-19)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from archiviste_workers.generate.router import router as generate_router

USER_ID = "11111111-1111-4111-8111-111111111111"
REQUEST_ID = "22222222-2222-4222-8222-222222222222"


@pytest_asyncio.fixture
async def client() -> Any:
    app = FastAPI()
    app.include_router(generate_router)
    app.state.retrieve_client = AsyncMock()
    app.state.llm_client = AsyncMock()
    app.state.conversation_client = AsyncMock()
    app.state.query_log_repo = AsyncMock()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _payload(**overrides: Any) -> dict[str, Any]:
    base = {
        "query": "Qui est l'Archiviste?",
        "conversation_id": None,
        "user_id": USER_ID,
        "user_tier": "anonymous",
        "request_id": REQUEST_ID,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_empty_query_400(client: AsyncClient) -> None:
    # AC-17.
    r = await client.post("/v1/generate", json=_payload(query=""))
    assert r.status_code == 400
    assert r.json() == {"error": "invalid_query"}


@pytest.mark.asyncio
async def test_oversize_query_400(client: AsyncClient) -> None:
    # AC-17: > 4 KiB UTF-8.
    big = "a" * (4 * 1024 + 1)
    r = await client.post("/v1/generate", json=_payload(query=big))
    assert r.status_code == 400
    assert r.json() == {"error": "invalid_query"}


@pytest.mark.asyncio
async def test_invalid_user_id_400(client: AsyncClient) -> None:
    # AC-18.
    r = await client.post("/v1/generate", json=_payload(user_id="not-a-uuid"))
    assert r.status_code == 400
    assert r.json() == {"error": "invalid_user_id"}


@pytest.mark.asyncio
async def test_missing_request_id_400(client: AsyncClient) -> None:
    # AC-18, OQ-7.
    payload = _payload()
    del payload["request_id"]
    r = await client.post("/v1/generate", json=payload)
    assert r.status_code == 400
    assert r.json() == {"error": "invalid_request_id"}


@pytest.mark.asyncio
async def test_invalid_user_tier_422(client: AsyncClient) -> None:
    # AC-19.
    r = await client.post("/v1/generate", json=_payload(user_tier="admin"))
    assert r.status_code == 422
    assert r.json() == {"error": "invalid_user_tier"}
