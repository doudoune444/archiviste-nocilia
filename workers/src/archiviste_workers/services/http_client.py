"""Shared httpx.AsyncClient (OQ-2). Lifespan-scoped, transport injectable for tests."""

from __future__ import annotations

import httpx

HTTP_TIMEOUT_S = 30.0


def build_async_client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=transport,
        timeout=HTTP_TIMEOUT_S,
        follow_redirects=False,
    )
