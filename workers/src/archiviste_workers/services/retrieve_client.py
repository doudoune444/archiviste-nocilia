"""Internal HTTP client to /v1/retrieve (RET-001). AC-3, AC-23."""

from __future__ import annotations

import httpx

from archiviste_workers.generate.models import Chunk

_HTTP_OK_CLASS = 2


class RetrieveError(RuntimeError):
    """Non-2xx or timeout from /v1/retrieve (AC-23)."""


class RetrieveClient:
    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")

    async def search(
        self, *, query: str, top_k: int, user_tier: str, request_id: str
    ) -> list[Chunk]:
        url = f"{self._base_url}/v1/retrieve"
        try:
            response = await self._client.post(
                url,
                json={"query": query, "top_k": top_k, "user_tier": user_tier},
                headers={"X-Request-Id": request_id},
            )
        except (httpx.HTTPError, OSError) as exc:
            raise RetrieveError(str(exc)) from exc
        if response.status_code // 100 != _HTTP_OK_CLASS:
            raise RetrieveError(f"retrieve status {response.status_code}")
        payload = response.json()
        raw_chunks = payload.get("chunks", []) if isinstance(payload, dict) else []
        return [Chunk.model_validate(item) for item in raw_chunks]
