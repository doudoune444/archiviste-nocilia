"""SqlTokenProvider — Cloud SQL IAM access token from the metadata server.

Fetches `sqlservice.admin` scoped OAuth token, caches with refresh-ahead
(REFRESH_AHEAD_SECONDS before expiry), wraps bearer in pydantic.SecretStr
(security.md §A09 — never logged in repr/str). Dedicated httpx.AsyncClient
with 5s total / 2s connect timeout (AC-7), never shared with the general
HTTP client.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

import httpx
from pydantic import SecretStr

METADATA_BASE_URL_DEFAULT = "http://metadata.google.internal"
# Metadata server path — not a password, despite linter heuristic.
METADATA_TOKEN_PATH = "/computeMetadata/v1/instance/service-accounts/default/token"  # noqa: S105
CLOUD_SQL_SCOPE = "https://www.googleapis.com/auth/sqlservice.admin"
REFRESH_AHEAD_SECONDS = 60
_HTTP_ERROR_THRESHOLD = 400

TokenReasonCode = Literal["timeout", "network", "metadata_token_failed"]


class TokenFetchError(Exception):
    """Raised when the metadata server returns an error or is unreachable.

    Carries a reason_code set at raise-site so callers can classify without
    substring-matching the message (MED-2).
    """

    def __init__(self, reason_code: TokenReasonCode) -> None:
        super().__init__(reason_code)
        self.reason_code: TokenReasonCode = reason_code


@dataclass
class _CachedToken:
    bearer: SecretStr
    expires_at: datetime  # UTC


class SqlTokenProvider:
    """Fetch and cache a Cloud SQL IAM access token from the metadata server.

    Each instance holds its own independent cache (AC-2 — no shared cache).
    Thread-safe via asyncio.Lock.
    """

    def __init__(
        self,
        *,
        base_url: str = METADATA_BASE_URL_DEFAULT,
        scope: str = CLOUD_SQL_SCOPE,
    ) -> None:
        self._base_url = base_url
        self._scope = scope
        # Dedicated client — AC-7 (not reused from services/http_client.py).
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0))
        self._lock = asyncio.Lock()
        self._cached: _CachedToken | None = None

    async def aclose(self) -> None:
        """Close the underlying HTTP client. Call at application shutdown."""
        await self._client.aclose()

    async def get_or_refresh(self) -> SecretStr:
        """Return cached token or fetch a fresh one.

        Refresh-ahead: if now >= expires_at - REFRESH_AHEAD_SECONDS, re-fetch.
        Double-checked locking avoids concurrent fetches.
        """
        cached = self._cached
        if cached is not None and self._is_cache_valid(cached):
            return cached.bearer
        async with self._lock:
            cached = self._cached
            if cached is not None and self._is_cache_valid(cached):
                return cached.bearer
            self._cached = await self._fetch()
            return self._cached.bearer

    def _is_cache_valid(self, cached: _CachedToken) -> bool:
        threshold = cached.expires_at - timedelta(seconds=REFRESH_AHEAD_SECONDS)
        return datetime.now(UTC) < threshold

    async def _fetch(self) -> _CachedToken:
        url = f"{self._base_url}{METADATA_TOKEN_PATH}"
        params = {"scopes": self._scope} if self._scope else None
        try:
            response = await self._client.get(
                url,
                headers={"Metadata-Flavor": "Google"},
                params=params,
            )
        except httpx.TimeoutException as exc:
            raise TokenFetchError("timeout") from exc
        except httpx.HTTPError as exc:
            raise TokenFetchError("network") from exc
        if response.status_code >= _HTTP_ERROR_THRESHOLD:
            raise TokenFetchError("metadata_token_failed")
        body = response.json()
        return _CachedToken(
            bearer=SecretStr(body["access_token"]),
            expires_at=datetime.now(UTC) + timedelta(seconds=int(body["expires_in"])),
        )
