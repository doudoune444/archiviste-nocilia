"""HTTP clients for /v1/retrieve and /v1/generate workers endpoints."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

import httpx
from pydantic import SecretStr

RETRIEVE_TIMEOUT_SECONDS = 60
GENERATE_TIMEOUT_SECONDS = 60
RETRIEVE_TOP_K = 5
# Seed corpus uses default access_tier='public' (migrations/0002_schema.sql).
# `anonymous` reads only `public` chunks → matches the eval seed.
EVAL_USER_TIER = "anonymous"
# Synthetic anonymous user identity for eval; /v1/generate requires X-User-Id (uuid)
# per the gateway→workers contract (specs/openapi/gateway-to-workers.yml §generate).
EVAL_USER_ID = "ec0de000-0000-4000-8000-000000000001"

# Retry policy for transient workers failures (EVAL-008).
# 4 attempts = 1 initial + 3 retries; backoff 1 → 2 → 4 s.
# Retryable: 429/5xx status codes and network timeouts. Non-retryable: other 4xx.
_RETRY_MAX_ATTEMPTS = 4
_RETRY_BACKOFF_BASE_SECONDS = 1.0
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

# Indirection so tests can monkeypatch without importing the `time` module directly.
_sleep: Callable[[float], None] = time.sleep


@dataclass(frozen=True)
class RetrievedChunk:
    """A chunk returned by /v1/retrieve."""

    source_path: str
    text: str


@dataclass(frozen=True)
class RetrieveResponse:
    """Successful response from /v1/retrieve."""

    chunks: list[RetrievedChunk]


@dataclass(frozen=True)
class GenerateResponse:
    """Successful response from /v1/generate."""

    answer: str
    citations: list[str]


@dataclass(frozen=True)
class EntryError:
    """Per-entry error (AC-15)."""

    status: Literal["timeout", "upstream_error", "malformed"]
    detail: str


def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff: 1, 2, 4 s (doubles per attempt)."""
    return _RETRY_BACKOFF_BASE_SECONDS * (2.0**attempt)


def _retry_after_seconds(resp: httpx.Response) -> float | None:
    """Parse Retry-After header as integer seconds; return None if absent or non-integer."""
    header = resp.headers.get("Retry-After")
    if header is None:
        return None
    try:
        return float(int(header))
    except ValueError:
        return None


def _post_with_retry(
    url: str,
    json: dict[str, object],
    headers: dict[str, str],
    timeout: float,
) -> httpx.Response | EntryError:
    """POST url with bounded retry-with-backoff for transient failures.

    Retries on httpx.TimeoutException and HTTP 429/5xx responses. Non-retryable
    4xx (e.g. 400/401/403) are returned immediately without retry. After
    _RETRY_MAX_ATTEMPTS exhausted, returns the last EntryError. If a 429 response
    carries a Retry-After header (integer seconds), that value overrides the
    computed backoff. _sleep is module-level so tests can monkeypatch it.
    """
    last_error: EntryError | None = None
    for attempt in range(_RETRY_MAX_ATTEMPTS):
        is_last_attempt = attempt == _RETRY_MAX_ATTEMPTS - 1
        try:
            resp = httpx.post(url, json=json, headers=headers, timeout=timeout)
        except httpx.TimeoutException:
            last_error = EntryError(status="timeout", detail="timeout")
            if not is_last_attempt:
                _sleep(_backoff_seconds(attempt))
            continue
        except httpx.HTTPError as exc:
            return EntryError(status="upstream_error", detail=str(exc))

        if resp.status_code in _RETRY_STATUSES:
            last_error = EntryError(
                status="upstream_error", detail=f"status={resp.status_code}"
            )
            if not is_last_attempt:
                wait = _retry_after_seconds(resp) or _backoff_seconds(attempt)
                _sleep(wait)
            continue

        return resp

    return last_error or EntryError(status="upstream_error", detail="exhausted")


@dataclass
class RetrieveClient:
    """Sync HTTP client for /v1/retrieve (AC-3, AC-15)."""

    base_url: str
    timeout: float = RETRIEVE_TIMEOUT_SECONDS
    auth_header: SecretStr | None = field(default=None)

    def search(
        self,
        query: str,
        request_id: str,
    ) -> RetrieveResponse | EntryError:
        """Call POST /v1/retrieve and return chunks or an EntryError."""
        headers: dict[str, str] = {"X-Request-Id": request_id}
        if self.auth_header is not None:
            headers["Authorization"] = f"Bearer {self.auth_header.get_secret_value()}"

        result = _post_with_retry(
            url=f"{self.base_url}/v1/retrieve",
            json={"query": query, "top_k": RETRIEVE_TOP_K, "user_tier": EVAL_USER_TIER},
            headers=headers,
            timeout=self.timeout,
        )
        if isinstance(result, EntryError):
            return result
        if not result.is_success:
            return EntryError(status="upstream_error", detail=f"status={result.status_code}")

        try:
            data = result.json()
            chunks = [
                RetrievedChunk(
                    source_path=c["source_path"],
                    text=c.get("text", c.get("chunk_text", "")),
                )
                for c in data.get("chunks", [])
            ]
            return RetrieveResponse(chunks=chunks)
        except (KeyError, ValueError, TypeError) as exc:
            return EntryError(status="malformed", detail=str(exc))


@dataclass
class GenerateClient:
    """Sync HTTP client for /v1/generate (AC-3, AC-15)."""

    base_url: str
    timeout: float = GENERATE_TIMEOUT_SECONDS
    auth_header: SecretStr | None = field(default=None)

    def generate(
        self,
        query: str,
        request_id: str,
    ) -> GenerateResponse | EntryError:
        """Call POST /v1/generate and return answer or an EntryError.

        Headers X-User-Id and X-User-Tier are required by the workers contract
        (specs/openapi/gateway-to-workers.yml); user_id/user_tier are NOT body fields.
        """
        headers: dict[str, str] = {
            "X-Request-Id": request_id,
            "X-User-Id": EVAL_USER_ID,
            "X-User-Tier": EVAL_USER_TIER,
        }
        if self.auth_header is not None:
            headers["Authorization"] = f"Bearer {self.auth_header.get_secret_value()}"
        payload: dict[str, object] = {
            "query": query,
            "request_id": request_id,
        }

        result = _post_with_retry(
            url=f"{self.base_url}/v1/generate",
            json=payload,
            headers=headers,
            timeout=self.timeout,
        )
        if isinstance(result, EntryError):
            return result
        if not result.is_success:
            return EntryError(status="upstream_error", detail=f"status={result.status_code}")

        try:
            data = result.json()
            return GenerateResponse(
                answer=data["answer"],
                citations=data.get("citations", []),
            )
        except (KeyError, ValueError, TypeError) as exc:
            return EntryError(status="malformed", detail=str(exc))
