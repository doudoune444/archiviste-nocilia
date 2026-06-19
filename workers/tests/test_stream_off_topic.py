"""Regression test: off_topic streaming path must yield token events.

CHAT-001 AC-1: SSE event grammar is meta -> token* -> (done | error).
The off_topic mode must emit at least one `token` event between `meta` and
`done`, matching the canon / mystery / lore_gap modes. This test would have
caught the TECH-230 regression where the token-yield loop was missing.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage

from archiviste_workers.generate.stream_router import stream_router
from archiviste_workers.services.conversation_client import ConversationClient
from archiviste_workers.services.http_client import build_async_client
from archiviste_workers.services.retrieve_client import RetrieveClient

USER_ID = "00000000-0000-0000-0000-000000000002"
REQUEST_ID = "55555555-5555-4555-8555-555555555556"


class _OffTopicLlmClient:
    """Fake LLM client that classifies as off_topic and streams a refusal."""

    def __init__(self, *, refusal_tokens: list[str]) -> None:
        self._refusal_tokens = refusal_tokens
        self.model = "mistral-small-latest"
        self.provider = "mistral"

    async def invoke(self, messages: list[Any], *, timeout_s: float | None = None) -> AIMessage:
        # Classifier call (timeout_s <= 5.0) returns off_topic intent.
        return AIMessage(
            content="off_topic",
            usage_metadata={"input_tokens": 10, "output_tokens": 1, "total_tokens": 11},
        )

    async def astream(
        self, messages: list[Any], *, timeout_s: float | None = None
    ) -> AsyncIterator[tuple[str, AIMessage | None]]:
        for token in self._refusal_tokens:
            yield (token, None)
        yield (
            "",
            AIMessage(
                content="".join(self._refusal_tokens),
                usage_metadata={"input_tokens": 20, "output_tokens": 5, "total_tokens": 25},
            ),
        )


def _conversation_ok(_: httpx.Request) -> httpx.Response:
    return httpx.Response(201, json={"ok": True})


def _build_off_topic_app(llm_client: _OffTopicLlmClient) -> FastAPI:
    app = FastAPI()
    app.include_router(stream_router)

    retrieve_transport = httpx.MockTransport(lambda _req: httpx.Response(200, json={"chunks": []}))
    conversation_transport = httpx.MockTransport(_conversation_ok)
    retrieve_http = build_async_client(transport=retrieve_transport)
    conversation_http = build_async_client(transport=conversation_transport)

    app.state.retrieve_client = RetrieveClient(retrieve_http, "http://retrieve.local")
    app.state.conversation_client = ConversationClient(conversation_http, "http://convo.local")
    app.state.llm_client = llm_client

    repo = AsyncMock()
    repo.insert = AsyncMock(return_value=True)
    app.state.query_log_repo = repo

    return app


def _parse_sse_events(raw: str) -> list[dict[str, Any]]:
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
async def test_off_topic_streaming_yields_token_events() -> None:
    # AC-1: off_topic must emit meta -> token* -> done, with at least one token event.
    # Regression guard: TECH-230 removed the token-yield loop from _stream_off_topic,
    # causing off_topic to emit meta -> done with zero token events.
    refusal_tokens = ["Je", " ne", " peux", " pas", " répondre."]
    llm = _OffTopicLlmClient(refusal_tokens=refusal_tokens)
    app = _build_off_topic_app(llm)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/v1/generate/stream",
            json={
                "query": "Quel est le cours du Bitcoin?",
                "conversation_id": None,
                "request_id": REQUEST_ID,
            },
            headers={"X-User-Id": USER_ID, "X-User-Tier": "anonymous"},
        )

    assert resp.status_code == 200
    events = _parse_sse_events(resp.text)
    event_names = [e["event"] for e in events]

    # AC-1: grammar is meta -> token* -> done.
    assert event_names[0] == "meta", f"Expected meta first, got: {event_names}"
    assert event_names[-1] == "done", f"Expected done last, got: {event_names}"

    # Regression: at least one token event must be present between meta and done.
    token_events = [e for e in events if e["event"] == "token"]
    assert len(token_events) >= 1, (
        "off_topic streaming path emitted zero token events — "
        "token-yield loop is missing (TECH-230 regression)"
    )
    assert len(token_events) == len(refusal_tokens)

    # meta carries mode=off_topic.
    assert events[0]["data"]["mode"] == "off_topic"

    # Concatenated token text matches the refusal.
    full_text = "".join(e["data"]["text"] for e in token_events)
    assert full_text == "".join(refusal_tokens)
