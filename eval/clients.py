"""HTTP clients for /v1/retrieve and /v1/generate workers endpoints."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import httpx

RETRIEVE_TIMEOUT_SECONDS = 60
GENERATE_TIMEOUT_SECONDS = 60
RETRIEVE_TOP_K = 5


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


@dataclass
class RetrieveClient:
    """Sync HTTP client for /v1/retrieve (AC-3, AC-15)."""

    base_url: str
    timeout: float = RETRIEVE_TIMEOUT_SECONDS
    _extra_headers: dict[str, str] = field(default_factory=dict, repr=False)

    def search(
        self,
        query: str,
        request_id: str,
    ) -> RetrieveResponse | EntryError:
        """Call POST /v1/retrieve and return chunks or an EntryError."""
        headers = {"X-Request-Id": request_id, **self._extra_headers}
        try:
            resp = httpx.post(
                f"{self.base_url}/v1/retrieve",
                json={"query": query, "top_k": RETRIEVE_TOP_K},
                headers=headers,
                timeout=self.timeout,
            )
        except httpx.TimeoutException:
            return EntryError(status="timeout", detail="retrieve timeout")
        except httpx.HTTPError as exc:
            return EntryError(status="upstream_error", detail=str(exc))

        if not resp.is_success:
            return EntryError(status="upstream_error", detail=f"status={resp.status_code}")

        try:
            data = resp.json()
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
    _extra_headers: dict[str, str] = field(default_factory=dict, repr=False)

    def generate(
        self,
        query: str,
        request_id: str,
        contexts: list[str] | None = None,
    ) -> GenerateResponse | EntryError:
        """Call POST /v1/generate and return answer or an EntryError."""
        headers = {"X-Request-Id": request_id, **self._extra_headers}
        payload: dict[str, object] = {"query": query, "top_k": RETRIEVE_TOP_K}
        if contexts is not None:
            payload["contexts"] = contexts
        try:
            resp = httpx.post(
                f"{self.base_url}/v1/generate",
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )
        except httpx.TimeoutException:
            return EntryError(status="timeout", detail="generate timeout")
        except httpx.HTTPError as exc:
            return EntryError(status="upstream_error", detail=str(exc))

        if not resp.is_success:
            return EntryError(status="upstream_error", detail=f"status={resp.status_code}")

        try:
            data = resp.json()
            return GenerateResponse(
                answer=data["answer"],
                citations=data.get("citations", []),
            )
        except (KeyError, ValueError, TypeError) as exc:
            return EntryError(status="malformed", detail=str(exc))
