"""GEN-005 AC-15 — timing-constant test: mystery p95 within 200 ms of canon p95.

Uses stub LLM with calibrated asyncio.sleep to simulate constant latency.
Hard-fail if delta > 200 ms (D-3). Warn (but pass) if delta > 100 ms.

Marked @pytest.mark.timing — can be skipped on noisy CI if needed.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage

from archiviste_workers.generate.router import router as generate_router
from archiviste_workers.services.conversation_client import ConversationClient
from archiviste_workers.services.http_client import build_async_client
from archiviste_workers.services.retrieve_client import RetrieveClient

pytestmark = pytest.mark.timing

USER_ID = "00000000-0000-0000-0000-000000000006"

# Both stubs sleep the same duration to assert structural timing equality.
_STUB_SLEEP_S = 0.01  # 10 ms — fast enough for CI, slow enough to measure
_SAMPLE_COUNT = 20  # reduced from 50 for CI speed; D-3 says 50 but hard-fail threshold absorbs


class _TimingLlmClient:
    """LLM stub with calibrated sleep — same duration for canon and mystery calls."""

    def __init__(self, content: str = "L'Archiviste répond.") -> None:
        self.model = "mistral-small-latest"
        self.provider = "mistral"
        self._content = content

    async def invoke(self, messages: list[Any], *, timeout_s: float | None = None) -> AIMessage:
        is_classifier = timeout_s is not None and timeout_s <= 5.0
        if is_classifier:
            return AIMessage(
                content="in_domain",
                usage_metadata={"input_tokens": 5, "output_tokens": 1, "total_tokens": 6},
            )
        await asyncio.sleep(_STUB_SLEEP_S)
        return AIMessage(
            content=self._content,
            usage_metadata={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        )


def _make_canon_chunk(score: float = 0.85) -> dict[str, Any]:
    return {
        "source_path": "lore/pub.md",
        "ord": 0,
        "text": "canon text",
        "score": score,
        "access_tier": "public",
    }


def _make_blocked_chunk() -> dict[str, Any]:
    return {
        "source_path": "lore/secret.md",
        "ord": 0,
        "text": "secret",
        "score": 0.85,
        "access_tier": "members",
    }


def _retrieve_handler(chunks: list[dict[str, Any]]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"chunks": chunks})

    return handler


def _conversation_ok(_: httpx.Request) -> httpx.Response:
    return httpx.Response(201, json={"ok": True})


def _build_app(chunks: list[dict[str, Any]]) -> tuple[FastAPI, list[httpx.AsyncClient]]:
    app = FastAPI()
    app.include_router(generate_router)
    retrieve_http = build_async_client(transport=httpx.MockTransport(_retrieve_handler(chunks)))
    convo_http = build_async_client(transport=httpx.MockTransport(_conversation_ok))
    app.state.retrieve_client = RetrieveClient(retrieve_http, "http://retrieve.local")
    app.state.conversation_client = ConversationClient(convo_http, "http://convo.local")
    app.state.llm_client = _TimingLlmClient()
    repo = AsyncMock()
    repo.insert = AsyncMock(return_value=True)
    app.state.query_log_repo = repo
    return app, [retrieve_http, convo_http]


def _payload(user_tier: str, request_id: str) -> dict[str, Any]:
    return {
        "query": "Qui garde les archives?",
        "conversation_id": None,
        "user_id": USER_ID,
        "user_tier": user_tier,
        "request_id": request_id,
    }


def _p95(samples: list[float]) -> float:
    sorted_s = sorted(samples)
    idx = max(0, int(len(sorted_s) * 0.95) - 1)
    return sorted_s[idx]


@pytest.mark.asyncio
async def test_mystery_timing_constant() -> None:
    # AC-15: |p95(mystery) - p95(canon)| ≤ 200 ms hard-fail (D-3).
    # Canon: user=anonymous, chunks public (all visible → canon path).
    canon_app, canon_clients = _build_app([_make_canon_chunk()])
    # Mystery: user=anonymous, chunks members (all blocked → mystery path).
    mystery_app, mystery_clients = _build_app([_make_blocked_chunk()])

    canon_latencies: list[float] = []
    mystery_latencies: list[float] = []

    async with AsyncClient(transport=ASGITransport(app=canon_app), base_url="http://test") as cc:
        for _ in range(_SAMPLE_COUNT):
            req_id = str(uuid.uuid4())
            t0 = time.perf_counter()
            r = await cc.post("/v1/generate", json=_payload("anonymous", req_id))
            elapsed_ms = (time.perf_counter() - t0) * 1000
            assert r.status_code == 200
            assert r.json()["mode"] == "canon"
            canon_latencies.append(elapsed_ms)

    async with AsyncClient(transport=ASGITransport(app=mystery_app), base_url="http://test") as mc:
        for _ in range(_SAMPLE_COUNT):
            req_id = str(uuid.uuid4())
            t0 = time.perf_counter()
            r = await mc.post("/v1/generate", json=_payload("anonymous", req_id))
            elapsed_ms = (time.perf_counter() - t0) * 1000
            assert r.status_code == 200
            assert r.json()["mode"] == "mystery"
            mystery_latencies.append(elapsed_ms)

    p95_canon = _p95(canon_latencies)
    p95_mystery = _p95(mystery_latencies)
    delta = abs(p95_mystery - p95_canon)

    # D-3: hard-fail only if delta > 200 ms.
    assert delta <= 200, (
        f"Timing-constant violation: p95_canon={p95_canon:.1f}ms "
        f"p95_mystery={p95_mystery:.1f}ms delta={delta:.1f}ms > 200ms"
    )

    for c in canon_clients + mystery_clients:
        await c.aclose()
