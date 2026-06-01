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
    base: dict[str, Any] = {
        "query": "Qui est l'Archiviste?",
        "conversation_id": None,
        "request_id": REQUEST_ID,
    }
    base.update(overrides)
    return base


def _headers(**overrides: Any) -> dict[str, str]:
    base = {
        "X-User-Id": USER_ID,
        "X-User-Tier": "anonymous",
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_empty_query_400(client: AsyncClient) -> None:
    # AC-17.
    r = await client.post("/v1/generate", json=_payload(query=""), headers=_headers())
    assert r.status_code == 400
    assert r.json() == {"error": "invalid_query"}


@pytest.mark.asyncio
async def test_oversize_query_400(client: AsyncClient) -> None:
    # AC-17: > 4 KiB UTF-8.
    big = "a" * (4 * 1024 + 1)
    r = await client.post("/v1/generate", json=_payload(query=big), headers=_headers())
    assert r.status_code == 400
    assert r.json() == {"error": "invalid_query"}


@pytest.mark.asyncio
async def test_missing_x_user_id_400(client: AsyncClient) -> None:
    # AC-14 / SEC-001: X-User-Id header absent → 400 invalid_user_id.
    hdrs = _headers()
    del hdrs["X-User-Id"]
    r = await client.post("/v1/generate", json=_payload(), headers=hdrs)
    assert r.status_code == 400
    assert r.json() == {"error": "invalid_user_id"}


@pytest.mark.asyncio
async def test_malformed_x_user_id_400(client: AsyncClient) -> None:
    # AC-14 / SEC-001: non-UUID value in X-User-Id → 400 invalid_user_id.
    r = await client.post(
        "/v1/generate", json=_payload(), headers=_headers(**{"X-User-Id": "not-a-uuid"})
    )
    assert r.status_code == 400
    assert r.json() == {"error": "invalid_user_id"}


@pytest.mark.asyncio
async def test_missing_x_user_tier_422(client: AsyncClient) -> None:
    # AC-14 / SEC-001: X-User-Tier header absent → 422 invalid_user_tier.
    hdrs = _headers()
    del hdrs["X-User-Tier"]
    r = await client.post("/v1/generate", json=_payload(), headers=hdrs)
    assert r.status_code == 422
    assert r.json() == {"error": "invalid_user_tier"}


@pytest.mark.asyncio
async def test_invalid_x_user_tier_422(client: AsyncClient) -> None:
    # AC-14 / SEC-001: unknown tier value in X-User-Tier → 422 invalid_user_tier.
    r = await client.post(
        "/v1/generate",
        json=_payload(),
        headers=_headers(**{"X-User-Tier": "admin"}),
    )
    assert r.status_code == 422
    assert r.json() == {"error": "invalid_user_tier"}


@pytest.mark.asyncio
async def test_missing_request_id_400(client: AsyncClient) -> None:
    # AC-18, OQ-7.
    payload = _payload()
    del payload["request_id"]
    r = await client.post("/v1/generate", json=payload, headers=_headers())
    assert r.status_code == 400
    assert r.json() == {"error": "invalid_request_id"}


@pytest.mark.asyncio
async def test_header_wins_over_body_identity(client: AsyncClient) -> None:
    # AC-14: when legacy caller sends user_id/user_tier in body, headers take precedence.
    # Body carries an invalid user_id; headers carry a valid one — must not 400.
    body = _payload()
    body["user_id"] = "not-a-uuid"
    body["user_tier"] = "bad-tier"
    r = await client.post("/v1/generate", json=body, headers=_headers())
    assert r.status_code != 400 or r.json().get("error") not in {
        "invalid_user_id",
        "invalid_user_tier",
    }
