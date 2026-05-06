"""Structured logging tests for `POST /v1/retrieve` (RET-001 AC-16).

Asserts the JSON log shape AND the absence of forbidden fields (`query`, `text`,
`embedding`, raw DB error strings) for every status code in the AC-16 enum.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from structlog.testing import capture_logs

from archiviste_workers.retrieve.router import router as retrieve_router

_REQUIRED_FIELDS = {
    "event",
    "query_len",
    "top_k",
    "user_tier",
    "results",
    "embedding_ms",
    "search_ms",
    "status",
}
_FORBIDDEN_FIELDS = {"query", "text", "embedding"}


class _StubEmbedder:
    model_name = "stub"

    def encode_batch(self, texts: list[str], batch_size: int) -> list[list[float]]:
        del batch_size
        return [[0.0] * 1023 + [1.0] for _ in texts]


class _OutagePool:
    def acquire(self) -> Any:
        raise OSError("stub outage — must NOT leak in body or log")


def _assert_log_shape(entry: dict[str, Any], status: str) -> None:
    """Verify AC-16 schema compliance: required fields present, forbidden absent."""
    assert entry["event"] == "retrieve"
    missing = _REQUIRED_FIELDS - set(entry.keys())
    assert not missing, f"missing fields: {missing}"
    assert entry["status"] == status
    for forbidden in _FORBIDDEN_FIELDS:
        assert forbidden not in entry, f"forbidden key {forbidden!r} present"


def _build_app(*, embedder_set: bool, pool: object) -> FastAPI:
    app = FastAPI()
    app.include_router(retrieve_router)
    app.state.embedder = _StubEmbedder() if embedder_set else None
    app.state.db_pool = pool
    return app


@pytest_asyncio.fixture
async def asgi_client_factory() -> AsyncIterator[Any]:
    """Yield a factory that builds AsyncClients bound to a given app, auto-closed."""
    clients: list[AsyncClient] = []

    async def _factory(app: FastAPI) -> AsyncClient:
        ac = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        clients.append(ac)
        return ac

    yield _factory
    for client in clients:
        await client.aclose()


async def _post(client: AsyncClient, payload: dict[str, Any]) -> None:
    await client.post("/v1/retrieve", json=payload)


@pytest.mark.asyncio
async def test_log_invalid_query(asgi_client_factory: Any) -> None:
    """AC-16: invalid_query → log status=invalid_query, no `query` field."""
    app = _build_app(embedder_set=True, pool=_OutagePool())
    client = await asgi_client_factory(app)
    with capture_logs() as logs:
        await _post(client, {"query": "secret-needle", "user_tier": "anonymous"})
        # query "" — strip empty -> invalid_query
        await _post(client, {"query": "", "user_tier": "anonymous"})
    retrieve_logs = [entry for entry in logs if entry.get("event") == "retrieve"]
    invalid = next(entry for entry in retrieve_logs if entry["status"] == "invalid_query")
    _assert_log_shape(invalid, status="invalid_query")
    assert "secret-needle" not in json.dumps(retrieve_logs)


@pytest.mark.asyncio
async def test_log_invalid_top_k(asgi_client_factory: Any) -> None:
    """AC-16: invalid_top_k → log status=invalid_top_k, no DB error leak."""
    app = _build_app(embedder_set=True, pool=_OutagePool())
    client = await asgi_client_factory(app)
    with capture_logs() as logs:
        await _post(client, {"query": "ok", "top_k": 99, "user_tier": "anonymous"})
    retrieve_logs = [entry for entry in logs if entry.get("event") == "retrieve"]
    _assert_log_shape(retrieve_logs[0], status="invalid_top_k")
    assert "stub outage" not in json.dumps(retrieve_logs)


@pytest.mark.asyncio
async def test_log_invalid_user_tier(asgi_client_factory: Any) -> None:
    """AC-16: invalid_user_tier → log status=invalid_user_tier."""
    app = _build_app(embedder_set=True, pool=_OutagePool())
    client = await asgi_client_factory(app)
    with capture_logs() as logs:
        await _post(client, {"query": "ok", "user_tier": "ROOT"})
    retrieve_logs = [entry for entry in logs if entry.get("event") == "retrieve"]
    _assert_log_shape(retrieve_logs[0], status="invalid_user_tier")


@pytest.mark.asyncio
async def test_log_embedder_unavailable(asgi_client_factory: Any) -> None:
    """AC-16: state.embedder=None → status=embedder_unavailable, no query leak."""
    app = _build_app(embedder_set=False, pool=_OutagePool())
    client = await asgi_client_factory(app)
    with capture_logs() as logs:
        await _post(client, {"query": "secret-needle-not-in-log", "user_tier": "anonymous"})
    retrieve_logs = [entry for entry in logs if entry.get("event") == "retrieve"]
    entry = retrieve_logs[0]
    _assert_log_shape(entry, status="embedder_unavailable")
    assert "secret-needle-not-in-log" not in json.dumps(retrieve_logs)


@pytest.mark.asyncio
async def test_log_database_unavailable(asgi_client_factory: Any) -> None:
    """AC-16: pool fails → status=database_unavailable, no query/DB error leak."""
    app = _build_app(embedder_set=True, pool=_OutagePool())
    client = await asgi_client_factory(app)
    with capture_logs() as logs:
        await _post(client, {"query": "another-needle", "user_tier": "anonymous"})
    retrieve_logs = [entry for entry in logs if entry.get("event") == "retrieve"]
    entry = retrieve_logs[0]
    _assert_log_shape(entry, status="database_unavailable")
    serialized = json.dumps(retrieve_logs)
    assert "another-needle" not in serialized
    assert "stub outage" not in serialized
