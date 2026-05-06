"""Unit tests for `POST /v1/retrieve` request validation (RET-001).

Covers AC-2 (schema + extra ignored), AC-3 (query bounds), AC-4 (top_k bounds),
AC-5 (user_tier whitelist), and AC-13 (embedder unavailable). No DB call must
happen for any failing-validation case: we wire `app.state.db_pool` to a tracker
that raises on `acquire()`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from archiviste_workers.retrieve.router import router as retrieve_router


class _TrackingPool:
    """Stand-in pool: records `acquire()` calls and raises OSError to surface
    a clean 503 (`database_unavailable`) without exercising real DB."""

    def __init__(self) -> None:
        self.acquire_calls = 0

    def acquire(self) -> Any:
        self.acquire_calls += 1
        raise OSError("simulated DB outage for unit test")


class _StaticEmbedder:
    """Deterministic 1024-dim embedder stub. Returns a unit vector quickly."""

    model_name = "stub"

    def encode_batch(self, texts: list[str], batch_size: int) -> list[list[float]]:
        del batch_size
        return [[0.0] * 1023 + [1.0] for _ in texts]


@pytest_asyncio.fixture
async def app_with_embedder() -> AsyncIterator[FastAPI]:
    app = FastAPI()
    app.include_router(retrieve_router)
    app.state.embedder = _StaticEmbedder()
    app.state.db_pool = _TrackingPool()
    yield app


@pytest_asyncio.fixture
async def client_with_embedder(app_with_embedder: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app_with_embedder)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def client_without_embedder() -> AsyncIterator[AsyncClient]:
    app = FastAPI()
    app.include_router(retrieve_router)
    app.state.embedder = None
    app.state.db_pool = _TrackingPool()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _valid_payload() -> dict[str, Any]:
    return {"query": "ok", "top_k": 5, "user_tier": "anonymous"}


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({}, "invalid_request"),
        ({"query": "ok"}, "invalid_request"),
        ({"user_tier": "anonymous"}, "invalid_request"),
        ({"query": "", "user_tier": "anonymous"}, "invalid_query"),
        ({"query": "   ", "user_tier": "anonymous"}, "invalid_query"),
        ({"query": 42, "user_tier": "anonymous"}, "invalid_query"),
        ({"query": "x" * 4097, "user_tier": "anonymous"}, "invalid_query"),
        ({"query": "ok", "top_k": 0, "user_tier": "anonymous"}, "invalid_top_k"),
        ({"query": "ok", "top_k": 21, "user_tier": "anonymous"}, "invalid_top_k"),
        ({"query": "ok", "top_k": "5", "user_tier": "anonymous"}, "invalid_top_k"),
        ({"query": "ok", "top_k": True, "user_tier": "anonymous"}, "invalid_top_k"),
        ({"query": "ok", "user_tier": "ROOT"}, "invalid_user_tier"),
        ({"query": "ok", "user_tier": "member"}, "invalid_user_tier"),
        ({"query": "ok", "user_tier": ""}, "invalid_user_tier"),
    ],
)
@pytest.mark.asyncio
async def test_validation_rejects_invalid_payload(
    app_with_embedder: FastAPI,
    client_with_embedder: AsyncClient,
    payload: dict[str, Any],
    code: str,
) -> None:
    """AC-2/3/4/5: invalid bodies map to a stable error code; DB never touched."""
    response = await client_with_embedder.post("/v1/retrieve", json=payload)
    assert response.status_code == 400
    assert response.json() == {"error": code}
    assert response.headers["content-type"] == "application/json; charset=utf-8"
    pool: _TrackingPool = app_with_embedder.state.db_pool
    assert pool.acquire_calls == 0


@pytest.mark.asyncio
async def test_extra_field_silently_ignored(client_with_embedder: AsyncClient) -> None:
    """AC-2: extra keys are ignored, validation passes (DB layer then 503)."""
    payload = {**_valid_payload(), "extra": "ignored", "another": [1, 2, 3]}
    response = await client_with_embedder.post("/v1/retrieve", json=payload)
    assert response.status_code == 503
    assert response.json() == {"error": "database_unavailable"}


@pytest.mark.asyncio
async def test_top_k_omitted_defaults_to_five(client_with_embedder: AsyncClient) -> None:
    """AC-4: missing `top_k` is accepted (passes validation; reaches DB layer)."""
    payload = {"query": "ok", "user_tier": "anonymous"}
    response = await client_with_embedder.post("/v1/retrieve", json=payload)
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_top_k_null_defaults_to_five(client_with_embedder: AsyncClient) -> None:
    """AC-4: explicit null → default 5 (passes validation; reaches DB layer)."""
    payload = {"query": "ok", "top_k": None, "user_tier": "anonymous"}
    response = await client_with_embedder.post("/v1/retrieve", json=payload)
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_embedder_unavailable_returns_503(
    client_without_embedder: AsyncClient,
) -> None:
    """AC-13: `state.embedder=None` → 503 embedder_unavailable, no DB call."""
    response = await client_without_embedder.post("/v1/retrieve", json=_valid_payload())
    assert response.status_code == 503
    assert response.json() == {"error": "embedder_unavailable"}
    assert response.headers["content-type"] == "application/json; charset=utf-8"


@pytest.mark.asyncio
async def test_malformed_json_returns_invalid_request(
    client_with_embedder: AsyncClient,
) -> None:
    """AC-2: non-JSON body → 400 invalid_request."""
    response = await client_with_embedder.post(
        "/v1/retrieve",
        content=b"not json",
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json() == {"error": "invalid_request"}
