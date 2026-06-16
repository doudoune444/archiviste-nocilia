"""CTR-001 — contradiction verification: vote threshold, ticket reuse, ACL re-resolve.

AC coverage:
  - three refute-biased judges; >=2 confirmations required (vote threshold)
  - confirmed path raises a lore-gap ticket carrying the visitor's claim verbatim
  - unconfirmed path raises no ticket
  - refute-biased verdict parsing (ambiguous/empty -> refute)
  - cited sources re-resolved server-side, ACL-bounded by caller tier (DB-backed)
  - request validation maps to the contract error enum
"""

from __future__ import annotations

import uuid
from collections import deque
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage

from archiviste_workers.contradiction import service as service_module
from archiviste_workers.contradiction.models import Citation
from archiviste_workers.contradiction.prompt import is_confirmation
from archiviste_workers.contradiction.repository import resolve_cited_sources
from archiviste_workers.contradiction.router import router as contradiction_router
from archiviste_workers.contradiction.service import verify_contradiction
from archiviste_workers.embedder import FakeEmbedder
from archiviste_workers.ingest.repository import (
    ChunkRecord,
    DocumentRecord,
    insert_document_with_chunks,
)
from archiviste_workers.services.llm import LlmTimeoutError
from archiviste_workers.services.ticket_service import TicketResult

_embedder = FakeEmbedder()

CONV_ID = "33333333-3333-4333-8333-333333333333"
REQUEST_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
USER_ID = "00000000-0000-4000-8000-000000000000"
CLAIM = "La source A dit que l'Archiviste est mort, la source B qu'il est vivant."
_SOURCES: list[tuple[str, int, str]] = [
    ("lore/a.md", 0, "L'Archiviste est mort."),
    ("lore/b.md", 0, "L'Archiviste est vivant."),
]
_CITATIONS = [
    Citation(source_path="lore/a.md", chunk_ords=[0]),
    Citation(source_path="lore/b.md", chunk_ords=[0]),
]


class _FakeJudgeLlm:
    """One configured verdict per invoke; judges run concurrently so order is irrelevant."""

    def __init__(self, verdicts: list[str], *, raise_all: bool = False) -> None:
        self._verdicts = deque(verdicts)
        self._raise_all = raise_all
        self.calls = 0
        self.model = "fake"
        self.provider = "fake"

    async def invoke(self, messages: list[Any], *, timeout_s: float | None = None) -> AIMessage:
        self.calls += 1
        if self._raise_all:
            raise LlmTimeoutError("boom")
        return AIMessage(content=self._verdicts.popleft())


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("CONTRADICTION", True),
        ("CONTRADICTION.", True),
        ("**CONTRADICTION**", True),
        ("  contradiction  ", True),
        ("NO_CONTRADICTION", False),
        ("no_contradiction", False),
        ("NO_CONTRADICTION.", False),
        # French / spaced natural-language refusals must NOT count as confirmations
        # (the substring CONTRADICTION is present but the verdict is a refusal).
        ("no contradiction", False),
        ("Aucune contradiction", False),
        ("pas de contradiction", False),
        # Verbose replies that bury the token are refute-biased to NO.
        ("Il y a CONTRADICTION claire", False),
        ("", False),
        ("Je ne sais pas", False),
    ],
)
def test_is_confirmation_refute_biased(reply: str, expected: bool) -> None:
    assert is_confirmation(reply) is expected


@pytest.fixture
def _stub_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_resolve(
        pool: Any, citations: Any, user_tier: str
    ) -> list[tuple[str, int, str]]:
        return _SOURCES

    monkeypatch.setattr(service_module, "resolve_cited_sources", _fake_resolve)


@pytest.fixture
def ticket_spy(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    calls: list[dict[str, str]] = []

    async def _fake_create(
        pool: Any, embedder: Any, *, conversation_id: str, question: str, request_id: str
    ) -> TicketResult:
        calls.append({"conversation_id": conversation_id, "question": question})
        return TicketResult(action="created", ticket_id="ticket-1", priority_score=1)

    monkeypatch.setattr(service_module, "create_or_increment", _fake_create)
    return calls


async def _run(llm: Any, *, tier: str = "anonymous") -> Any:
    return await verify_contradiction(
        pool=object(),
        embedder=_embedder,
        llm=llm,
        claim=CLAIM,
        conversation_id=CONV_ID,
        citations=_CITATIONS,
        user_tier=tier,
        request_id=REQUEST_ID,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("verdicts", "confirmed", "confirmations"),
    [
        (["NO_CONTRADICTION", "NO_CONTRADICTION", "NO_CONTRADICTION"], False, 0),
        (["CONTRADICTION", "NO_CONTRADICTION", "NO_CONTRADICTION"], False, 1),
        (["CONTRADICTION", "CONTRADICTION", "NO_CONTRADICTION"], True, 2),
        (["CONTRADICTION", "CONTRADICTION", "CONTRADICTION"], True, 3),
    ],
)
@pytest.mark.usefixtures("_stub_sources")
async def test_vote_threshold(
    ticket_spy: list[dict[str, str]],
    verdicts: list[str],
    confirmed: bool,
    confirmations: int,
) -> None:
    result = await _run(_FakeJudgeLlm(verdicts))

    assert result.contradiction_confirmed is confirmed
    assert result.confirmations == confirmations
    if confirmed:
        assert len(ticket_spy) == 1
        assert ticket_spy[0]["question"] == CLAIM  # ticket carries the claim verbatim
        assert ticket_spy[0]["conversation_id"] == CONV_ID
        assert result.ticket_action == "created"
        assert result.ticket_id == "ticket-1"
    else:
        assert ticket_spy == []
        assert result.ticket_action == "not_raised"
        assert result.ticket_id is None


@pytest.mark.asyncio
@pytest.mark.usefixtures("_stub_sources")
async def test_judge_failure_counts_as_refute(ticket_spy: list[dict[str, str]]) -> None:
    result = await _run(_FakeJudgeLlm([], raise_all=True))

    assert result.contradiction_confirmed is False
    assert result.confirmations == 0
    assert ticket_spy == []


@pytest.mark.asyncio
async def test_no_visible_sources_skips_judges(
    monkeypatch: pytest.MonkeyPatch, ticket_spy: list[dict[str, str]]
) -> None:
    async def _empty(pool: Any, citations: Any, user_tier: str) -> list[tuple[str, int, str]]:
        return []

    monkeypatch.setattr(service_module, "resolve_cited_sources", _empty)
    llm = _FakeJudgeLlm(["CONTRADICTION", "CONTRADICTION", "CONTRADICTION"])

    result = await _run(llm)

    assert result.contradiction_confirmed is False
    assert llm.calls == 0  # no judge runs when nothing is visible to verify against
    assert ticket_spy == []


async def _seed_chunk(pool: asyncpg.Pool, source_path: str, tier: str, text: str) -> None:
    await insert_document_with_chunks(
        pool,
        DocumentRecord(
            source_path=source_path,
            title=source_path,
            tags=[],
            access_tier=tier,
            content_hash=str(uuid.uuid4()),
        ),
        [ChunkRecord(ord=0, text=text, embedding=_embedder.encode_batch([text], batch_size=1)[0])],
    )


@pytest.mark.asyncio
async def test_resolve_cited_sources_acl_drops_above_tier(clean_db: asyncpg.Pool) -> None:
    await _seed_chunk(clean_db, "lore/public.md", "public", "Texte public.")
    await _seed_chunk(clean_db, "lore/secret.md", "author_only", "Texte secret.")
    citations = [
        Citation(source_path="lore/public.md", chunk_ords=[0]),
        Citation(source_path="lore/secret.md", chunk_ords=[0]),
    ]

    anon = await resolve_cited_sources(clean_db, citations, "anonymous")
    author = await resolve_cited_sources(clean_db, citations, "author_only")

    assert anon == [("lore/public.md", 0, "Texte public.")]  # author_only dropped
    assert sorted(author) == [
        ("lore/public.md", 0, "Texte public."),
        ("lore/secret.md", 0, "Texte secret."),
    ]


async def _ensure_conversation(pool: asyncpg.Pool, conv_id: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO conversations (id, user_id, gcs_uri) VALUES ($1, $2, $3) "
            "ON CONFLICT DO NOTHING",
            conv_id,
            "00000000-0000-0000-0000-000000000000",
            f"gs://test/{conv_id}.md",
        )


async def _confirmed_verify(pool: asyncpg.Pool, conversation_id: str) -> Any:
    """Confirmed path through the REAL resolver + ticket_service (no monkeypatch)."""
    return await verify_contradiction(
        pool=pool,
        embedder=_embedder,
        llm=_FakeJudgeLlm(["CONTRADICTION", "CONTRADICTION", "CONTRADICTION"]),
        claim=CLAIM,
        conversation_id=conversation_id,
        citations=[Citation(source_path="lore/x.md", chunk_ords=[0])],
        user_tier="anonymous",
        request_id=REQUEST_ID,
    )


@pytest.mark.asyncio
async def test_confirmed_creates_then_increments_real_ticket(clean_db: asyncpg.Pool) -> None:
    conv = "44444444-4444-4444-8444-444444444444"
    await _ensure_conversation(clean_db, conv)
    async with clean_db.acquire() as conn:
        await conn.execute("DELETE FROM tickets")
    await _seed_chunk(clean_db, "lore/x.md", "public", "L'Archiviste est mort.")

    first = await _confirmed_verify(clean_db, conv)
    second = await _confirmed_verify(clean_db, conv)

    assert first.contradiction_confirmed is True
    assert first.ticket_action == "created"
    assert first.ticket_id is not None
    assert second.ticket_action == "incremented"  # same claim → cosine dedup increments
    assert second.ticket_id == first.ticket_id
    async with clean_db.acquire() as conn:
        await conn.execute("DELETE FROM tickets")


@pytest.mark.asyncio
async def test_confirmed_unknown_conversation_yields_skipped_error(clean_db: asyncpg.Pool) -> None:
    # FK miss: confirmed, but conversation_id absent → tickets FK RESTRICT → create_or_increment
    # is fail-soft → skipped_error surfaced as ticket_action (no ticket, no raise).
    await _seed_chunk(clean_db, "lore/x.md", "public", "L'Archiviste est mort.")

    result = await _confirmed_verify(clean_db, "55555555-5555-4555-8555-555555555555")

    assert result.contradiction_confirmed is True
    assert result.confirmations == 3
    assert result.ticket_action == "skipped_error"
    assert result.ticket_id is None


@pytest_asyncio.fixture
async def verify_client() -> AsyncIterator[AsyncClient]:
    app = FastAPI()
    app.include_router(contradiction_router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _valid_body() -> dict[str, Any]:
    return {
        "claim": CLAIM,
        "conversation_id": CONV_ID,
        "citations": [{"source_path": "lore/a.md", "chunk_ords": [0]}],
        "request_id": REQUEST_ID,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutate", "headers", "expected_error"),
    [
        (lambda b: b.pop("citations"), True, "invalid_citations"),
        (lambda b: b.update(citations=[]), True, "invalid_citations"),
        (lambda b: b.update(conversation_id="not-a-uuid"), True, "invalid_conversation_id"),
        (lambda b: b.update(claim=""), True, "invalid_claim"),
        (lambda b: None, False, "invalid_request"),
    ],
)
async def test_request_validation_maps_to_contract_errors(
    verify_client: AsyncClient,
    mutate: Any,
    headers: bool,
    expected_error: str,
) -> None:
    body = _valid_body()
    mutate(body)
    request_headers = {"x-user-id": USER_ID, "x-user-tier": "anonymous"} if headers else {}

    resp = await verify_client.post("/v1/verify-contradiction", json=body, headers=request_headers)

    assert resp.status_code == 400
    assert resp.json() == {"error": expected_error}
