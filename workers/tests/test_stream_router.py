"""CHAT-001 streaming endpoint tests.

AC references:
- AC-1: SSE event grammar meta -> token* -> (done | error) — in correct order.
- AC-2: meta emitted first, before any token.
- AC-3: conversation persisted + lore-gap ticket ONLY after clean done.
- AC-4: on mid-stream LLM failure (LlmTimeoutError), emit error, NO persistence, NO ticket.
- AC-5: pre-stream validation errors return non-200 JSON (not SSE).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage

import archiviste_workers.generate.stream_router as stream_router_module
from archiviste_workers.embedder import FakeEmbedder
from archiviste_workers.generate.stream_router import stream_router
from archiviste_workers.services.conversation_client import ConversationClient
from archiviste_workers.services.http_client import build_async_client
from archiviste_workers.services.llm import LlmTimeoutError, LlmUpstreamError
from archiviste_workers.services.retrieve_client import RetrieveClient
from archiviste_workers.services.ticket_service import TicketResult

USER_ID = "00000000-0000-0000-0000-000000000001"
REQUEST_ID = "33333333-3333-4333-8333-333333333334"
CONVERSATION_ID = "44444444-4444-4444-8444-444444444445"


class _FakeStreamLlmClient:
    """Fake LLM client with async streaming support for CHAT-001 tests."""

    def __init__(
        self,
        *,
        tokens: list[str] | None = None,
        raise_timeout: bool = False,
        raise_upstream: int | None = None,
        classifier_response: str = "in_domain",
    ) -> None:
        self._tokens = tokens or ["Hello", " world", "!"]
        self._raise_timeout = raise_timeout
        self._raise_upstream = raise_upstream
        self._classifier_response = classifier_response
        self.model = "mistral-small-latest"
        self.provider = "mistral"
        self.invoke_calls: list[list[Any]] = []

    async def invoke(self, messages: list[Any], *, timeout_s: float | None = None) -> AIMessage:
        self.invoke_calls.append(messages)
        is_classifier = timeout_s is not None and timeout_s <= 5.0
        if is_classifier:
            return AIMessage(
                content=self._classifier_response,
                usage_metadata={"input_tokens": 10, "output_tokens": 1, "total_tokens": 11},
            )
        return AIMessage(
            content="".join(self._tokens),
            usage_metadata={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        )

    async def astream(
        self, messages: list[Any], *, timeout_s: float | None = None
    ) -> AsyncIterator[tuple[str, AIMessage | None]]:
        if self._raise_timeout:
            raise LlmTimeoutError("timeout")
        if self._raise_upstream is not None:
            raise LlmUpstreamError("upstream error", status_code=self._raise_upstream)
        for token in self._tokens:
            yield (token, None)
        yield (
            "",
            AIMessage(
                content="".join(self._tokens),
                usage_metadata={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
            ),
        )


def _retrieve_handler(chunks: list[dict[str, Any]] | None = None) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"chunks": chunks or []})

    return handler


def _retrieve_error_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(503, json={"error": "down"})


def _conversation_ok(_: httpx.Request) -> httpx.Response:
    return httpx.Response(201, json={"ok": True})


def _build_stream_app(
    *,
    llm_client: _FakeStreamLlmClient,
    chunks: list[dict[str, Any]] | None = None,
    conversation_handler: Any = _conversation_ok,
    retrieve_handler: Any = None,
) -> tuple[FastAPI, list[httpx.AsyncClient]]:
    app = FastAPI()
    app.include_router(stream_router)

    if retrieve_handler is None:
        retrieve_transport = httpx.MockTransport(_retrieve_handler(chunks))
    else:
        retrieve_transport = httpx.MockTransport(retrieve_handler)
    conversation_transport = httpx.MockTransport(conversation_handler)
    retrieve_http = build_async_client(transport=retrieve_transport)
    conversation_http = build_async_client(transport=conversation_transport)

    app.state.retrieve_client = RetrieveClient(retrieve_http, "http://retrieve.local")
    app.state.conversation_client = ConversationClient(conversation_http, "http://convo.local")
    app.state.llm_client = llm_client

    repo = AsyncMock()
    repo.insert = AsyncMock(return_value=True)
    app.state.query_log_repo = repo

    return app, [retrieve_http, conversation_http]


def _payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "query": "Qui est l'Archiviste de Nocilia?",
        "conversation_id": None,
        "request_id": REQUEST_ID,
    }
    base.update(overrides)
    return base


def _headers(user_tier: str = "anonymous") -> dict[str, str]:
    return {"X-User-Id": USER_ID, "X-User-Tier": user_tier}


def _parse_sse_events(raw: str) -> list[dict[str, Any]]:
    """Parse SSE text into list of {event, data} dicts."""
    events: list[dict[str, Any]] = []
    current_event: str | None = None
    current_data: str | None = None
    for line in raw.splitlines():
        if line.startswith("event: "):
            current_event = line[len("event: ") :]
        elif line.startswith("data: "):
            current_data = line[len("data: ") :]
        elif line == "" and current_event is not None and current_data is not None:
            events.append({"event": current_event, "data": json.loads(current_data)})
            current_event = None
            current_data = None
    return events


@pytest.mark.asyncio
async def test_stream_canon_meta_tokens_done_order() -> None:
    # AC-1, AC-2: meta first, then tokens, then done (canon path with high-score chunk).
    chunks = [
        {"source_path": "a/b.md", "ord": 0, "text": "alpha", "score": 0.80, "access_tier": "public"}
    ]
    llm = _FakeStreamLlmClient(tokens=["L'Archiviste", " consulte", " [a/b.md]"])
    app, http_clients = _build_stream_app(llm_client=llm, chunks=chunks)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/v1/generate/stream",
            json=_payload(conversation_id=CONVERSATION_ID),
            headers=_headers(),
        )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse_events(resp.text)
    event_names = [e["event"] for e in events]

    # AC-1: order is meta, token*, done.
    assert event_names[0] == "meta"
    assert event_names[-1] == "done"
    assert all(n == "token" for n in event_names[1:-1])

    # AC-2: meta carries mode, conversation_id, request_id.
    meta_data = events[0]["data"]
    assert meta_data["mode"] == "canon"
    assert meta_data["conversation_id"] == CONVERSATION_ID
    assert meta_data["request_id"] == REQUEST_ID

    # done carries citations, usage, timings.
    done_data = events[-1]["data"]
    assert "citations" in done_data
    assert "usage" in done_data
    assert "retrieve_ms" in done_data
    assert "llm_ms" in done_data

    for http in http_clients:
        await http.aclose()


@pytest.mark.asyncio
async def test_stream_lore_gap_no_ticket_without_db() -> None:
    # AC-3 (partial): lore_gap emits meta -> token* -> done (no db_pool → ticket skipped).
    chunks = [
        {
            "source_path": "x/y.md",
            "ord": 0,
            "text": "low relevance",
            "score": 0.10,
            "access_tier": "public",
        }
    ]
    llm = _FakeStreamLlmClient(tokens=["Je ne sais pas."])
    app, http_clients = _build_stream_app(llm_client=llm, chunks=chunks)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/v1/generate/stream",
            json=_payload(),
            headers=_headers(),
        )

    assert resp.status_code == 200
    events = _parse_sse_events(resp.text)
    assert events[0]["data"]["mode"] == "lore_gap"
    assert events[-1]["event"] == "done"
    for http in http_clients:
        await http.aclose()


@pytest.mark.asyncio
async def test_stream_llm_timeout_emits_error_no_persistence() -> None:
    # AC-4: LlmTimeoutError mid-stream → error SSE event, NO conversation persistence, NO ticket.
    chunks = [
        {"source_path": "a/b.md", "ord": 0, "text": "alpha", "score": 0.80, "access_tier": "public"}
    ]
    llm = _FakeStreamLlmClient(raise_timeout=True)
    conversation_mock = AsyncMock()
    conversation_mock.append_message = AsyncMock()

    app, http_clients = _build_stream_app(llm_client=llm, chunks=chunks)
    # Replace conversation_client with one tracking calls.
    conversation_transport = httpx.MockTransport(_conversation_ok)
    convo_http = build_async_client(transport=conversation_transport)
    fake_convo = AsyncMock()
    fake_convo.append_message = AsyncMock(return_value=MagicMock(ok=True))
    app.state.conversation_client = fake_convo

    ts_calls: list[int] = []

    async def _spy(*args: Any, **kwargs: Any) -> Any:
        ts_calls.append(1)
        return TicketResult(action="created", ticket_id="fake-id", priority_score=1)

    with patch.object(stream_router_module, "create_or_increment", side_effect=_spy):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/generate/stream",
                json=_payload(conversation_id=CONVERSATION_ID),
                headers=_headers(),
            )

    assert resp.status_code == 200
    events = _parse_sse_events(resp.text)

    # Last event must be error with llm_timeout code.
    assert events[-1]["event"] == "error"
    assert events[-1]["data"]["error"] == "llm_timeout"

    # AC-4: NO conversation persistence after error.
    fake_convo.append_message.assert_not_called()

    # FIX 1 + FIX 2: query_log row IS inserted even on failure.
    app.state.query_log_repo.insert.assert_called_once()
    inserted_row = app.state.query_log_repo.insert.call_args.args[0]
    assert inserted_row.status_code == 504
    assert inserted_row.mode == "canon"

    # FIX 2: create_or_increment must NOT be called on error.
    assert len(ts_calls) == 0

    for http in http_clients:
        await http.aclose()
    await convo_http.aclose()


@pytest.mark.asyncio
async def test_stream_llm_upstream_error_emits_error_no_persistence() -> None:
    # AC-4: LlmUpstreamError → error SSE, no persistence.
    chunks = [
        {"source_path": "a/b.md", "ord": 0, "text": "alpha", "score": 0.80, "access_tier": "public"}
    ]
    llm = _FakeStreamLlmClient(raise_upstream=503)

    app, http_clients = _build_stream_app(llm_client=llm, chunks=chunks)
    fake_convo = AsyncMock()
    fake_convo.append_message = AsyncMock(return_value=MagicMock(ok=True))
    app.state.conversation_client = fake_convo

    ts_calls: list[int] = []

    async def _spy(*args: Any, **kwargs: Any) -> Any:
        ts_calls.append(1)
        return TicketResult(action="created", ticket_id="fake-id", priority_score=1)

    with patch.object(stream_router_module, "create_or_increment", side_effect=_spy):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/generate/stream",
                json=_payload(conversation_id=CONVERSATION_ID),
                headers=_headers(),
            )

    assert resp.status_code == 200
    events = _parse_sse_events(resp.text)
    assert events[-1]["event"] == "error"
    assert events[-1]["data"]["error"] == "llm_upstream"
    fake_convo.append_message.assert_not_called()

    # FIX 1 + FIX 2: query_log row IS inserted even on failure.
    app.state.query_log_repo.insert.assert_called_once()
    inserted_row = app.state.query_log_repo.insert.call_args.args[0]
    assert inserted_row.status_code == 502
    assert inserted_row.mode == "canon"

    # FIX 2: create_or_increment must NOT be called on error.
    assert len(ts_calls) == 0

    for http in http_clients:
        await http.aclose()


@pytest.mark.asyncio
async def test_stream_missing_query_returns_400_json() -> None:
    # AC-5: missing query → 400 JSON envelope, NOT SSE.
    llm = _FakeStreamLlmClient()
    app, http_clients = _build_stream_app(llm_client=llm)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/v1/generate/stream",
            json={"conversation_id": None, "request_id": REQUEST_ID},
            headers=_headers(),
        )

    assert resp.status_code == 400
    body = resp.json()
    assert "error" in body
    # Must NOT be text/event-stream.
    assert "text/event-stream" not in resp.headers.get("content-type", "")

    for http in http_clients:
        await http.aclose()


@pytest.mark.asyncio
async def test_stream_invalid_user_tier_returns_422_json() -> None:
    # AC-5: invalid X-User-Tier → 422 JSON envelope, NOT SSE.
    llm = _FakeStreamLlmClient()
    app, http_clients = _build_stream_app(llm_client=llm)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/v1/generate/stream",
            json=_payload(),
            headers={"X-User-Id": USER_ID, "X-User-Tier": "superuser"},
        )

    assert resp.status_code == 422
    body = resp.json()
    assert "error" in body
    assert "text/event-stream" not in resp.headers.get("content-type", "")

    for http in http_clients:
        await http.aclose()


@pytest.mark.asyncio
async def test_stream_response_has_security_headers() -> None:
    # Security: X-Content-Type-Options: nosniff must be present.
    chunks = [
        {"source_path": "a/b.md", "ord": 0, "text": "alpha", "score": 0.80, "access_tier": "public"}
    ]
    llm = _FakeStreamLlmClient(tokens=["ok"])
    app, http_clients = _build_stream_app(llm_client=llm, chunks=chunks)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/v1/generate/stream",
            json=_payload(conversation_id=CONVERSATION_ID),
            headers=_headers(),
        )

    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("x-request-id") == REQUEST_ID

    for http in http_clients:
        await http.aclose()


# ---------------------------------------------------------------------------
# FIX 2: lore_gap done WITH db_pool+embedder — create_or_increment called once.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_lore_gap_done_calls_create_or_increment_once() -> None:
    # AC-3: after successful lore_gap done, create_or_increment called exactly once.
    # FIX 2: verify the no-ticket / ticket invariant for the streaming path.
    chunks = [
        {
            "source_path": "x/y.md",
            "ord": 0,
            "text": "low relevance",
            "score": 0.10,
            "access_tier": "public",
        }
    ]
    llm = _FakeStreamLlmClient(tokens=["Je ne sais pas."])
    fake_convo = AsyncMock()
    fake_convo.append_message = AsyncMock(return_value=MagicMock(ok=True))

    app, http_clients = _build_stream_app(llm_client=llm, chunks=chunks)
    app.state.conversation_client = fake_convo
    app.state.db_pool = object()  # non-None sentinel triggers ticket_service call
    app.state.embedder = FakeEmbedder()

    ts_calls: list[int] = []

    async def _spy_create(*args: Any, **kwargs: Any) -> Any:
        ts_calls.append(1)
        return TicketResult(action="created", ticket_id="fake-id", priority_score=1)

    with patch.object(stream_router_module, "create_or_increment", side_effect=_spy_create):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/generate/stream",
                json=_payload(conversation_id=CONVERSATION_ID),
                headers=_headers(),
            )

    assert resp.status_code == 200
    events = _parse_sse_events(resp.text)
    assert events[0]["data"]["mode"] == "lore_gap"
    assert events[-1]["event"] == "done"

    # AC-3: create_or_increment called exactly once after successful persistence.
    assert len(ts_calls) == 1

    for http in http_clients:
        await http.aclose()


# ---------------------------------------------------------------------------
# FIX 2: lore_gap LLM timeout — error event, no persistence, no ticket, failure log written.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_lore_gap_llm_timeout_error_no_ticket_no_persistence() -> None:
    # AC-4: lore_gap LlmTimeoutError → error SSE, no persistence, no ticket.
    # FIX 2: strengthen to assert create_or_increment NOT called.
    # FIX 1: assert failure query_log row IS inserted with correct shape.
    chunks = [
        {
            "source_path": "x/y.md",
            "ord": 0,
            "text": "low relevance",
            "score": 0.10,
            "access_tier": "public",
        }
    ]
    llm = _FakeStreamLlmClient(raise_timeout=True)
    fake_convo = AsyncMock()
    fake_convo.append_message = AsyncMock(return_value=MagicMock(ok=True))

    app, http_clients = _build_stream_app(llm_client=llm, chunks=chunks)
    app.state.conversation_client = fake_convo
    app.state.db_pool = object()
    app.state.embedder = FakeEmbedder()

    ts_calls: list[int] = []

    async def _spy_create(*args: Any, **kwargs: Any) -> Any:
        ts_calls.append(1)
        return TicketResult(action="created", ticket_id="fake-id", priority_score=1)

    with patch.object(stream_router_module, "create_or_increment", side_effect=_spy_create):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/generate/stream",
                json=_payload(conversation_id=CONVERSATION_ID),
                headers=_headers(),
            )

    assert resp.status_code == 200
    events = _parse_sse_events(resp.text)

    # AC-1: last event is error with llm_timeout.
    assert events[-1]["event"] == "error"
    assert events[-1]["data"]["error"] == "llm_timeout"

    # AC-4: NO conversation persistence after error.
    fake_convo.append_message.assert_not_called()

    # FIX 2: create_or_increment must NOT be called on error.
    assert len(ts_calls) == 0

    # FIX 1: failure query_log row IS inserted with lore_gap shape.
    app.state.query_log_repo.insert.assert_called_once()
    inserted_row = app.state.query_log_repo.insert.call_args.args[0]
    assert inserted_row.status_code == 504
    assert inserted_row.mode == "lore_gap"
    # AC-13 parity: classifier tokens recorded on lore_gap timeout.
    assert inserted_row.prompt_tokens == 10
    assert inserted_row.completion_tokens == 1

    for http in http_clients:
        await http.aclose()


@pytest.mark.asyncio
async def test_stream_lore_gap_llm_upstream_error_no_ticket_no_persistence() -> None:
    # AC-4: lore_gap LlmUpstreamError → error SSE, no persistence, no ticket.
    # FIX 2: strengthen to assert create_or_increment NOT called.
    # FIX 1: assert failure query_log row IS inserted with lore_gap shape, status=502.
    chunks = [
        {
            "source_path": "x/y.md",
            "ord": 0,
            "text": "low relevance",
            "score": 0.10,
            "access_tier": "public",
        }
    ]
    llm = _FakeStreamLlmClient(raise_upstream=503)
    fake_convo = AsyncMock()
    fake_convo.append_message = AsyncMock(return_value=MagicMock(ok=True))

    app, http_clients = _build_stream_app(llm_client=llm, chunks=chunks)
    app.state.conversation_client = fake_convo
    app.state.db_pool = object()
    app.state.embedder = FakeEmbedder()

    ts_calls: list[int] = []

    async def _spy_create(*args: Any, **kwargs: Any) -> Any:
        ts_calls.append(1)
        return TicketResult(action="created", ticket_id="fake-id", priority_score=1)

    with patch.object(stream_router_module, "create_or_increment", side_effect=_spy_create):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/generate/stream",
                json=_payload(conversation_id=CONVERSATION_ID),
                headers=_headers(),
            )

    assert resp.status_code == 200
    events = _parse_sse_events(resp.text)

    assert events[-1]["event"] == "error"
    assert events[-1]["data"]["error"] == "llm_upstream"
    fake_convo.append_message.assert_not_called()
    assert len(ts_calls) == 0

    # FIX 1: failure query_log IS inserted with lore_gap shape, status=502.
    app.state.query_log_repo.insert.assert_called_once()
    inserted_row = app.state.query_log_repo.insert.call_args.args[0]
    assert inserted_row.status_code == 502
    assert inserted_row.mode == "lore_gap"
    # AC-14 parity: classifier tokens recorded on lore_gap upstream error.
    assert inserted_row.prompt_tokens == 10
    assert inserted_row.completion_tokens == 1

    for http in http_clients:
        await http.aclose()


# ---------------------------------------------------------------------------
# FIX 4: retrieve failure emits error event with code retrieve_failed + failure log.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_retrieve_failure_emits_error_event() -> None:
    # FIX 4: retrieve failure → terminal error SSE with code "retrieve_failed".
    # No meta event precedes it (retrieve precedes mode decision).
    # FIX 1: failure query_log row IS inserted with status=502, mode=canon.
    llm = _FakeStreamLlmClient(tokens=["should not be reached"])
    fake_convo = AsyncMock()
    fake_convo.append_message = AsyncMock(return_value=MagicMock(ok=True))

    app, http_clients = _build_stream_app(
        llm_client=llm,
        retrieve_handler=_retrieve_error_handler,
    )
    app.state.conversation_client = fake_convo

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/v1/generate/stream",
            json=_payload(conversation_id=CONVERSATION_ID),
            headers=_headers(),
        )

    assert resp.status_code == 200
    events = _parse_sse_events(resp.text)

    # Contract: meta is emitted "once the mode is decided"; retrieve failure precedes that.
    # So the only event is a terminal error.
    assert len(events) == 1
    assert events[0]["event"] == "error"
    assert events[0]["data"]["error"] == "retrieve_failed"

    # AC-4 parity: no conversation persistence.
    fake_convo.append_message.assert_not_called()

    # FIX 1: failure query_log row IS inserted with status=502.
    app.state.query_log_repo.insert.assert_called_once()
    inserted_row = app.state.query_log_repo.insert.call_args.args[0]
    assert inserted_row.status_code == 502
    assert inserted_row.mode == "canon"
    assert inserted_row.prompt_tokens is None
    assert inserted_row.completion_tokens is None

    for http in http_clients:
        await http.aclose()
