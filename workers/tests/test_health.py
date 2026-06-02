"""Tests for /healthz (liveness) and /readyz (readiness) endpoints.

AC OPS-003:
- /readyz 200 + status=="ok" when DB pool acquire+SELECT 1 succeeds.
- /readyz 503 + status=="degraded" when pool raises on acquire/fetch.
- /healthz stays shallow (always 200/ok, no DB dependency).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from archiviste_workers.main import app
from archiviste_workers.routers import health

# ---------------------------------------------------------------------------
# Helpers — fake asyncpg pool (no live DB)
# ---------------------------------------------------------------------------


def _make_fake_pool(*, fetchval_raises: BaseException | None = None) -> Any:
    """Return a fake pool whose acquire() context manager yields a fake connection.

    If fetchval_raises is given, the fake connection's fetchval() raises that exception.
    """
    fake_conn = AsyncMock()
    if fetchval_raises is not None:
        fake_conn.fetchval = AsyncMock(side_effect=fetchval_raises)
    else:
        fake_conn.fetchval = AsyncMock(return_value=1)

    # asyncpg pool.acquire() is an async context manager.
    acquire_cm = MagicMock()
    acquire_cm.__aenter__ = AsyncMock(return_value=fake_conn)
    acquire_cm.__aexit__ = AsyncMock(return_value=False)

    fake_pool = MagicMock()
    fake_pool.acquire = MagicMock(return_value=acquire_cm)
    return fake_pool


def _make_app_with_pool(pool: Any) -> FastAPI:
    """Build a minimal FastAPI app with health router and a stubbed db_pool."""
    minimal_app = FastAPI()
    minimal_app.include_router(health.router)
    minimal_app.state.db_pool = pool
    return minimal_app


# ---------------------------------------------------------------------------
# AC OPS-003: /readyz — DB reachable → 200 ok
# ---------------------------------------------------------------------------


def test_readyz_returns_200_when_db_ok() -> None:
    """AC OPS-003: /readyz must return HTTP 200 with status=='ok' when SELECT 1 succeeds."""
    fake_pool = _make_fake_pool()
    test_app = _make_app_with_pool(fake_pool)
    client = TestClient(test_app, raise_server_exceptions=False)

    response = client.get("/readyz")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert isinstance(payload["version"], str)


# ---------------------------------------------------------------------------
# AC OPS-003: /readyz — DB unreachable → 503 degraded
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        asyncpg.PostgresError("connection refused"),
        OSError("network error"),
        TimeoutError("timeout"),
    ],
    ids=["asyncpg_error", "os_error", "timeout_error"],
)
def test_readyz_returns_503_when_db_fails(exc: BaseException) -> None:
    """AC OPS-003: /readyz must return HTTP 503 with status=='degraded' on DB failure."""
    fake_pool = _make_fake_pool(fetchval_raises=exc)
    test_app = _make_app_with_pool(fake_pool)
    client = TestClient(test_app, raise_server_exceptions=False)

    response = client.get("/readyz")

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "degraded"
    assert isinstance(payload["version"], str)


# ---------------------------------------------------------------------------
# Existing liveness test — must stay green (no regression)
# ---------------------------------------------------------------------------


def test_healthz_returns_ok() -> None:
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert isinstance(payload["version"], str)
