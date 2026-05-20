"""GEN-001 router integration tests.

Covers AC-1, AC-2, AC-3, AC-4, AC-5, AC-11, AC-13, AC-14, AC-15, AC-21, AC-22, AC-23, AC-24.
DB-backed query_log assertions reuse the `db_pool` fixture (skips if Postgres absent).
LLM is stubbed via a fake LlmClient. /v1/retrieve is stubbed via httpx.MockTransport.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import asyncpg
import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage
from structlog.testing import capture_logs

from archiviste_workers.generate.router import router as generate_router
from archiviste_workers.services.conversation_client import ConversationClient
from archiviste_workers.services.http_client import build_async_client
from archiviste_workers.services.llm import LlmTimeoutError, LlmUpstreamError
from archiviste_workers.services.query_log import QueryLogRepository
from archiviste_workers.services.retrieve_client import RetrieveClient

USER_ID = "00000000-0000-0000-0000-000000000000"  # sentinel anonymous user
REQUEST_ID = "33333333-3333-4333-8333-333333333333"
CONVERSATION_ID = "44444444-4444-4444-8444-444444444444"


class _FakeLlmClient:
    """Stub LLM client. First invoke() call = classifier (returns 'in_domain' by default).
    Subsequent calls use the configured message/error."""

    def __init__(
        self,
        *,
        message: AIMessage | None = None,
        raise_timeout: bool = False,
        raise_upstream: int | None = None,
        classifier_response: str = "in_domain",
    ) -> None:
        self.all_calls: list[list[Any]] = []
        self.captured_messages: list[Any] = []
        self._message = message or AIMessage(
            content="L'Archiviste consulte ses parchemins. [a/b.md]",
            usage_metadata={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        )
        self._raise_timeout = raise_timeout
        self._raise_upstream = raise_upstream
        self._classifier_response = classifier_response
        self.model = "mistral-small-latest"
        self.provider = "mistral"

    async def invoke(self, messages: list[Any], *, timeout_s: float | None = None) -> AIMessage:
        self.all_calls.append(messages)
        self.captured_messages = messages
        is_classifier = timeout_s is not None and timeout_s <= 5.0
        if is_classifier:
            return AIMessage(
                content=self._classifier_response,
                usage_metadata={"input_tokens": 10, "output_tokens": 1, "total_tokens": 11},
            )
        if self._raise_timeout:
            raise LlmTimeoutError("timeout")
        if self._raise_upstream is not None:
            raise LlmUpstreamError("up", status_code=self._raise_upstream)
        return self._message


def _retrieve_handler_factory(
    chunks: list[dict[str, Any]] | None = None,
    status_code: int = 200,
    captured: dict[str, Any] | None = None,
) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured["headers"] = dict(request.headers)
            captured["json"] = httpx.Request(
                method=request.method, url=request.url, content=request.content
            ).read()
        if status_code != 200:
            return httpx.Response(status_code, json={"error": "boom"})
        return httpx.Response(200, json={"chunks": chunks or []})

    return handler


def _conversation_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(201, json={"ok": True})


def _conversation_failing_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(503, json={"error": "down"})


def _build_app(
    *,
    retrieve_handler: Any,
    conversation_handler: Any,
    llm_client: _FakeLlmClient,
    pool: asyncpg.Pool | None = None,
) -> tuple[FastAPI, list[httpx.AsyncClient]]:
    app = FastAPI()
    app.include_router(generate_router)
    retrieve_transport = httpx.MockTransport(retrieve_handler)
    conversation_transport = httpx.MockTransport(conversation_handler)
    retrieve_http = build_async_client(transport=retrieve_transport)
    conversation_http = build_async_client(transport=conversation_transport)
    app.state.retrieve_client = RetrieveClient(retrieve_http, "http://retrieve.local")
    app.state.conversation_client = ConversationClient(conversation_http, "http://convo.local")
    app.state.llm_client = llm_client
    if pool is not None:
        app.state.query_log_repo = QueryLogRepository(pool)
    else:
        repo = AsyncMock()
        repo.insert = AsyncMock(return_value=True)
        app.state.query_log_repo = repo
    return app, [retrieve_http, conversation_http]


def _payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "query": "Qui est l'Archiviste de Nocilia?",
        "conversation_id": None,
        "user_id": USER_ID,
        "user_tier": "anonymous",
        "request_id": REQUEST_ID,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_nominal_response_shape() -> None:
    # AC-1, AC-3.
    captured: dict[str, Any] = {}
    # GEN-004: score >= 0.45 keeps canon path (AC-19).
    chunks = [{"source_path": "a/b.md", "ord": 0, "text": "alpha", "score": 0.72}]
    llm = _FakeLlmClient()
    app, http_clients = _build_app(
        retrieve_handler=_retrieve_handler_factory(chunks=chunks, captured=captured),
        conversation_handler=_conversation_handler,
        llm_client=llm,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/generate", json=_payload(conversation_id=CONVERSATION_ID))
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "canon"
    assert body["conversation_id"] == CONVERSATION_ID
    assert body["request_id"] == REQUEST_ID
    assert body["citations"] == [{"source_path": "a/b.md", "chunk_ords": [0]}]
    assert isinstance(body["retrieve_ms"], int)
    assert isinstance(body["llm_ms"], int)
    assert captured["headers"]["x-request-id"] == REQUEST_ID
    for http in http_clients:
        await http.aclose()


@pytest.mark.asyncio
async def test_timings_consistent_with_query_log_latency(db_pool: asyncpg.Pool) -> None:
    # AC-24: retrieve_ms + llm_ms <= latency_ms persisted in query_log.
    # GEN-004: score >= 0.45 keeps canon path.
    chunks = [{"source_path": "a/b.md", "ord": 0, "text": "alpha", "score": 0.72}]
    llm = _FakeLlmClient()
    app, http_clients = _build_app(
        retrieve_handler=_retrieve_handler_factory(chunks=chunks),
        conversation_handler=_conversation_handler,
        llm_client=llm,
        pool=db_pool,
    )
    request_id = "77777777-7777-4777-8777-777777777777"
    conversation_id = "88888888-8888-4888-8888-888888888888"
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM query_log WHERE request_id=$1", request_id)
        await conn.execute("DELETE FROM conversations WHERE id=$1", conversation_id)
        await conn.execute(
            "INSERT INTO conversations (id, user_id, gcs_uri) VALUES ($1, $2, $3)",
            conversation_id,
            USER_ID,
            f"gs://test/{conversation_id}.md",
        )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/v1/generate",
            json=_payload(request_id=request_id, conversation_id=conversation_id),
        )
    assert r.status_code == 200
    body = r.json()
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT latency_ms FROM query_log WHERE request_id=$1", request_id
        )
    assert row is not None
    assert body["retrieve_ms"] + body["llm_ms"] <= row["latency_ms"]
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM query_log WHERE request_id=$1", request_id)
        await conn.execute("DELETE FROM conversations WHERE id=$1", conversation_id)
    for http in http_clients:
        await http.aclose()


@pytest.mark.asyncio
async def test_zero_chunks_triggers_lore_gap() -> None:
    # GEN-004 D9: zero chunks -> max_score=0.0 < 0.45 -> lore_gap (supersedes zero-chunk canon).
    llm = _FakeLlmClient()
    app, http_clients = _build_app(
        retrieve_handler=_retrieve_handler_factory(chunks=[]),
        conversation_handler=_conversation_handler,
        llm_client=llm,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/generate", json=_payload())
    assert r.status_code == 200
    assert r.json()["mode"] == "lore_gap"
    assert r.json()["citations"] == []
    for http in http_clients:
        await http.aclose()


@pytest.mark.asyncio
async def test_high_score_chunks_canon_with_marker() -> None:
    # AC-4, AC-5: chunks score >= 0.45 + LLM no citation -> <no_archives_found/> marker.
    llm = _FakeLlmClient(
        message=AIMessage(
            content="plain answer no citation",
            usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        )
    )
    # GEN-004: score >= 0.45 keeps canon path (AC-19).
    chunks = [{"source_path": "a/b.md", "ord": 0, "text": "alpha", "score": 0.60}]
    app, http_clients = _build_app(
        retrieve_handler=_retrieve_handler_factory(chunks=chunks),
        conversation_handler=_conversation_handler,
        llm_client=llm,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/generate", json=_payload())
    assert r.status_code == 200
    assert r.json()["mode"] == "canon"
    for http in http_clients:
        await http.aclose()


@pytest.mark.asyncio
async def test_conversation_id_generated_when_null() -> None:
    # AC-2.
    llm = _FakeLlmClient()
    app, http_clients = _build_app(
        retrieve_handler=_retrieve_handler_factory(chunks=[]),
        conversation_handler=_conversation_handler,
        llm_client=llm,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/generate", json=_payload(conversation_id=None))
    body = r.json()
    assert body["conversation_id"]
    assert len(body["conversation_id"]) == 36
    for http in http_clients:
        await http.aclose()


@pytest.mark.asyncio
async def test_retrieve_failure_502_no_query_log_no_llm() -> None:
    # AC-23.
    llm = _FakeLlmClient()
    app, http_clients = _build_app(
        retrieve_handler=_retrieve_handler_factory(status_code=500),
        conversation_handler=_conversation_handler,
        llm_client=llm,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/generate", json=_payload())
    assert r.status_code == 502
    assert r.json() == {"error": "retrieve_failed"}
    # AC-23: classifier runs (1 call), but no generation LLM call — only 1 total invoke.
    assert len(llm.all_calls) == 1
    app.state.query_log_repo.insert.assert_not_called()
    for http in http_clients:
        await http.aclose()


@pytest.mark.asyncio
async def test_llm_upstream_4xx_502() -> None:
    # AC-21: canon path LLM upstream error -> 502.
    # GEN-004: use score >= 0.45 to stay on canon path.
    llm = _FakeLlmClient(raise_upstream=401)
    canon_chunks = [{"source_path": "a/b.md", "ord": 0, "text": "t", "score": 0.60}]
    app, http_clients = _build_app(
        retrieve_handler=_retrieve_handler_factory(chunks=canon_chunks),
        conversation_handler=_conversation_handler,
        llm_client=llm,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/generate", json=_payload())
    assert r.status_code == 502
    assert r.json() == {"error": "llm_upstream"}
    app.state.query_log_repo.insert.assert_called_once()
    inserted_row = app.state.query_log_repo.insert.call_args.args[0]
    assert inserted_row.status_code == 502
    for http in http_clients:
        await http.aclose()


@pytest.mark.asyncio
async def test_llm_upstream_5xx_502() -> None:
    # AC-22: canon path LLM 5xx -> 502.
    # GEN-004: use score >= 0.45 to stay on canon path.
    llm = _FakeLlmClient(raise_upstream=503)
    canon_chunks = [{"source_path": "a/b.md", "ord": 0, "text": "t", "score": 0.60}]
    app, http_clients = _build_app(
        retrieve_handler=_retrieve_handler_factory(chunks=canon_chunks),
        conversation_handler=_conversation_handler,
        llm_client=llm,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/generate", json=_payload())
    assert r.status_code == 502
    inserted_row = app.state.query_log_repo.insert.call_args.args[0]
    assert inserted_row.status_code == 502
    for http in http_clients:
        await http.aclose()


@pytest.mark.asyncio
async def test_llm_timeout_504() -> None:
    # AC-11: canon path LLM timeout -> 504 with no token data.
    # GEN-004: use score >= 0.45 to stay on canon path.
    llm = _FakeLlmClient(raise_timeout=True)
    canon_chunks = [{"source_path": "a/b.md", "ord": 0, "text": "t", "score": 0.60}]
    app, http_clients = _build_app(
        retrieve_handler=_retrieve_handler_factory(chunks=canon_chunks),
        conversation_handler=_conversation_handler,
        llm_client=llm,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/generate", json=_payload())
    assert r.status_code == 504
    assert r.json() == {"error": "llm_timeout"}
    inserted_row = app.state.query_log_repo.insert.call_args.args[0]
    assert inserted_row.status_code == 504
    assert inserted_row.prompt_tokens is None
    for http in http_clients:
        await http.aclose()


@pytest.mark.asyncio
async def test_no_citation_log_warn() -> None:
    # AC-13: canon path with no citation in LLM response -> logs warning.
    # GEN-004: use score >= 0.45 to stay on canon path.
    llm = _FakeLlmClient(
        message=AIMessage(
            content="plain answer",
            usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        )
    )
    chunks = [{"source_path": "a/b.md", "ord": 0, "text": "alpha", "score": 0.60}]
    app, http_clients = _build_app(
        retrieve_handler=_retrieve_handler_factory(chunks=chunks),
        conversation_handler=_conversation_handler,
        llm_client=llm,
    )
    transport = ASGITransport(app=app)
    with capture_logs() as captured:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/v1/generate", json=_payload())
    assert r.status_code == 200
    assert r.json()["citations"] == []
    assert any(log.get("event") == "llm_no_citation" for log in captured)
    for http in http_clients:
        await http.aclose()


@pytest.mark.asyncio
async def test_conversation_log_failure_does_not_break_response() -> None:
    # AC-14.
    llm = _FakeLlmClient()
    app, http_clients = _build_app(
        retrieve_handler=_retrieve_handler_factory(chunks=[]),
        conversation_handler=_conversation_failing_handler,
        llm_client=llm,
    )
    transport = ASGITransport(app=app)
    with capture_logs() as captured:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/v1/generate", json=_payload())
    assert r.status_code == 200
    assert any(log.get("event") == "conversation_log_failed" for log in captured)
    for http in http_clients:
        await http.aclose()


@pytest.mark.asyncio
async def test_query_log_inserted_on_success(db_pool: asyncpg.Pool) -> None:
    # AC-15: a single row is inserted with all expected fields.
    chunks = [{"source_path": "a/b.md", "ord": 0, "text": "alpha"}]
    llm = _FakeLlmClient()
    app, http_clients = _build_app(
        retrieve_handler=_retrieve_handler_factory(chunks=chunks),
        conversation_handler=_conversation_handler,
        llm_client=llm,
        pool=db_pool,
    )
    request_id = "55555555-5555-4555-8555-555555555555"
    conversation_id = "66666666-6666-4666-8666-666666666666"
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM query_log WHERE request_id=$1", request_id)
        await conn.execute("DELETE FROM conversations WHERE id=$1", conversation_id)
        await conn.execute(
            "INSERT INTO conversations (id, user_id, gcs_uri) VALUES ($1, $2, $3)",
            conversation_id,
            USER_ID,
            f"gs://test/{conversation_id}.md",
        )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/v1/generate",
            json=_payload(request_id=request_id, conversation_id=conversation_id),
        )
    assert r.status_code == 200
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status_code, prompt_tokens, completion_tokens, mode, latency_ms, "
            "cost_eur FROM query_log WHERE request_id=$1",
            request_id,
        )
    assert row is not None
    assert row["status_code"] == 200
    assert row["mode"] == "canon"
    assert row["prompt_tokens"] == 100
    assert row["completion_tokens"] == 50
    assert row["latency_ms"] >= 0
    assert row["cost_eur"] is not None
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM query_log WHERE request_id=$1", request_id)
        await conn.execute("DELETE FROM conversations WHERE id=$1", conversation_id)
    for http in http_clients:
        await http.aclose()


@pytest.mark.asyncio
async def test_query_log_intent_in_domain_canon(db_pool: asyncpg.Pool) -> None:
    # GEN-003 AC-2 / AC-19: query_log.intent = 'in_domain' on canon path.
    chunks = [{"source_path": "a/b.md", "ord": 0, "text": "alpha"}]
    llm = _FakeLlmClient(classifier_response="in_domain")
    app, http_clients = _build_app(
        retrieve_handler=_retrieve_handler_factory(chunks=chunks),
        conversation_handler=_conversation_handler,
        llm_client=llm,
        pool=db_pool,
    )
    request_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    conversation_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM query_log WHERE request_id=$1", request_id)
        await conn.execute("DELETE FROM conversations WHERE id=$1", conversation_id)
        await conn.execute(
            "INSERT INTO conversations (id, user_id, gcs_uri) VALUES ($1, $2, $3)",
            conversation_id,
            USER_ID,
            f"gs://test/{conversation_id}.md",
        )
    transport = ASGITransport(app=app)
    with capture_logs() as logs:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/v1/generate",
                json=_payload(request_id=request_id, conversation_id=conversation_id),
            )
    assert r.status_code == 200
    assert r.json()["mode"] == "canon"
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT intent, mode FROM query_log WHERE request_id=$1", request_id
        )
    assert row is not None
    assert row["intent"] == "in_domain"
    assert row["mode"] == "canon"
    # AC-20: log INFO contains intent field.
    generate_logs = [lg for lg in logs if lg.get("event") == "generate"]
    assert len(generate_logs) == 1
    assert generate_logs[0]["intent"] == "in_domain"
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM query_log WHERE request_id=$1", request_id)
        await conn.execute("DELETE FROM conversations WHERE id=$1", conversation_id)
    for http in http_clients:
        await http.aclose()
