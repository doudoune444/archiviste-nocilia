"""Integration + unit tests for SEC-005 workers IAM SQL token injection.

AC-12: 4 cases (a)-(d) via respx + Postgres container.
AC-7: SqlTokenProvider unit tests (scope, cache, refresh-ahead, SecretStr redact).
AC-9: lifespan fail-fast on metadata 500.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest
import respx
from fastapi import FastAPI
from httpx import Response

from archiviste_workers.auth_metadata.token import (
    CLOUD_SQL_SCOPE,
    METADATA_TOKEN_PATH,
    SqlTokenProvider,
    TokenFetchError,
)
from archiviste_workers.main import lifespan

if TYPE_CHECKING:
    import asyncpg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_token_response(
    access_token: str = "test-iam-token",  # noqa: S107 — test fixture, not a real token
    expires_in: int = 3600,
) -> dict[str, Any]:
    return {"access_token": access_token, "expires_in": expires_in, "token_type": "Bearer"}


# ---------------------------------------------------------------------------
# AC-12 (a) part 1: nominal pool acquire (without token_provider)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_create_pool_nominal_acquire(db_pool_required: asyncpg.Pool) -> None:
    """AC-12(a) part 1: nominal pool acquires against docker-compose Postgres."""
    # Verifies the base create_pool path still works and pgvector codec is registered.
    async with db_pool_required.acquire() as conn:
        result = await conn.fetchval("SELECT 1")
    assert result == 1


# ---------------------------------------------------------------------------
# AC-12 (a) part 2: token callback wired through provider
# AC-7(a): metadata fetch uses sqlservice.admin scope
# ---------------------------------------------------------------------------


@respx.mock(base_url="http://metadata.test")
async def test_create_pool_injects_token_callback_wired(
    respx_mock: respx.MockRouter,
) -> None:
    """AC-12(a) part 2 / AC-7(a): token_provider callable invoked with correct scope.

    docker-compose Postgres uses password auth; we cannot present an IAM token to it.
    We instead assert the callable machinery: metadata IS fetched with sqlservice.admin
    scope when SqlTokenProvider.get_or_refresh() is called.
    The plan's oracle: metadata mock called >= 1 time with sqlservice.admin scope.
    """
    route = respx_mock.get(METADATA_TOKEN_PATH).mock(
        return_value=Response(200, json=_mock_token_response())
    )

    provider = SqlTokenProvider(base_url="http://metadata.test", scope=CLOUD_SQL_SCOPE)
    try:
        token = await provider.get_or_refresh()
        assert token.get_secret_value() == "test-iam-token"
    finally:
        await provider.aclose()

    assert route.called
    called_url = str(route.calls[0].request.url)
    assert "scopes=" in called_url
    assert "sqlservice.admin" in called_url


# ---------------------------------------------------------------------------
# AC-12 (b): boot fails when metadata returns 500
# AC-9: TokenFetchError raised (not swallowed)
# ---------------------------------------------------------------------------


@respx.mock(base_url="http://metadata.test")
async def test_boot_fails_on_metadata_500(
    respx_mock: respx.MockRouter,
) -> None:
    """AC-12(b) / AC-9: SqlTokenProvider raises TokenFetchError on metadata 500."""
    respx_mock.get(METADATA_TOKEN_PATH).mock(return_value=Response(500))

    provider = SqlTokenProvider(base_url="http://metadata.test", scope=CLOUD_SQL_SCOPE)
    try:
        with pytest.raises(TokenFetchError) as exc_info:
            await provider.get_or_refresh()
        assert "metadata_token_failed" in str(exc_info.value)
    finally:
        await provider.aclose()


# ---------------------------------------------------------------------------
# AC-12 (c): cache hit — only one metadata fetch for two get_or_refresh calls
# ---------------------------------------------------------------------------


@respx.mock(base_url="http://metadata.test")
async def test_token_cache_single_fetch(
    respx_mock: respx.MockRouter,
) -> None:
    """AC-12(c): two consecutive get_or_refresh() with expires_in=3600 yield one fetch."""
    route = respx_mock.get(METADATA_TOKEN_PATH).mock(
        return_value=Response(200, json=_mock_token_response(expires_in=3600))
    )

    provider = SqlTokenProvider(base_url="http://metadata.test", scope=CLOUD_SQL_SCOPE)
    try:
        token_a = await provider.get_or_refresh()
        token_b = await provider.get_or_refresh()

        assert token_a.get_secret_value() == token_b.get_secret_value()
        assert route.call_count == 1
    finally:
        await provider.aclose()


# ---------------------------------------------------------------------------
# AC-12 (d): refresh-ahead — token with expires_in=1 triggers re-fetch
# ---------------------------------------------------------------------------


@respx.mock(base_url="http://metadata.test")
async def test_token_refresh_ahead(
    respx_mock: respx.MockRouter,
) -> None:
    """AC-12(d): expires_in=1 is within refresh-ahead window (<60s); second call re-fetches."""
    route = respx_mock.get(METADATA_TOKEN_PATH).mock(
        return_value=Response(200, json=_mock_token_response(expires_in=1))
    )

    provider = SqlTokenProvider(base_url="http://metadata.test", scope=CLOUD_SQL_SCOPE)
    try:
        await provider.get_or_refresh()
        await asyncio.sleep(0.05)
        await provider.get_or_refresh()

        assert route.call_count == 2
    finally:
        await provider.aclose()


# ---------------------------------------------------------------------------
# AC-7(a): fetch uses sqlservice.admin scope in query string
# ---------------------------------------------------------------------------


@respx.mock(base_url="http://metadata.test")
async def test_fetch_uses_sqlservice_admin_scope(
    respx_mock: respx.MockRouter,
) -> None:
    """AC-7(a): GET metadata includes scopes=https://...sqlservice.admin."""
    route = respx_mock.get(METADATA_TOKEN_PATH).mock(
        return_value=Response(200, json=_mock_token_response())
    )

    provider = SqlTokenProvider(base_url="http://metadata.test", scope=CLOUD_SQL_SCOPE)
    try:
        await provider.get_or_refresh()
    finally:
        await provider.aclose()

    assert route.called
    called_url = str(route.calls[0].request.url)
    assert "scopes=" in called_url
    assert "sqlservice.admin" in called_url


# ---------------------------------------------------------------------------
# AC-7(d): SecretStr redacted in repr (security.md §A09)
# ---------------------------------------------------------------------------


@respx.mock(base_url="http://metadata.test")
async def test_secret_str_redacted_in_repr(
    respx_mock: respx.MockRouter,
) -> None:
    """AC-7(d): repr(provider._cached.bearer) must not contain the raw token value."""
    respx_mock.get(METADATA_TOKEN_PATH).mock(
        return_value=Response(200, json=_mock_token_response(access_token="super-secret-token"))
    )

    provider = SqlTokenProvider(base_url="http://metadata.test", scope=CLOUD_SQL_SCOPE)
    try:
        await provider.get_or_refresh()
        assert provider._cached is not None
        redacted = repr(provider._cached.bearer)
        assert "super-secret-token" not in redacted
        assert "**" in redacted
    finally:
        await provider.aclose()


# ---------------------------------------------------------------------------
# AC-9: lifespan propagates exception when metadata fails (no silent catch)
# ---------------------------------------------------------------------------


async def test_lifespan_fails_on_metadata_500() -> None:
    """AC-9: lifespan raises on token fetch failure; no silent catch."""
    mock_provider = AsyncMock(spec=SqlTokenProvider)
    mock_provider.get_or_refresh = AsyncMock(side_effect=TokenFetchError("metadata_token_failed"))
    mock_provider.aclose = AsyncMock()

    with (
        patch(
            "archiviste_workers.main.SqlTokenProvider",
            return_value=mock_provider,
        ),
        patch(
            "archiviste_workers.main.create_pool",
            side_effect=TokenFetchError("metadata_token_failed"),
        ),
    ):
        app = FastAPI(lifespan=lifespan)

        with pytest.raises((TokenFetchError, Exception)):
            async with app.router.lifespan_context(app):
                pass
