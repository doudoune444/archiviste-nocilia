"""#162 — 4-way verdict judging: vote threshold, ticket policy, retrieval fallback, redaction.

AC references:
  - present verdict (>=2 votes) → no ticket raised
  - absent verdict (>=2 votes) → ticket raised
  - contradiction verdict (>=2 votes) → ticket raised
  - unclear >=2 → ticket raised
  - no-citation retrieval path → judges run against retrieved chunks
  - judge failure → unclear (fail-safe)
  - redaction: emitted reason must not contain chunk body text
  - ACL: re-resolution drops chunks above caller tier
  - request validation maps to contract error enum
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
from archiviste_workers.contradiction.models import Citation, VerifyContradictionRequest
from archiviste_workers.contradiction.prompt import parse_verdict
from archiviste_workers.contradiction.repository import resolve_cited_sources
from archiviste_workers.contradiction.router import router as contradiction_router
from archiviste_workers.contradiction.service import verify_contradiction
from archiviste_workers.embedder import FakeEmbedder
from archiviste_workers.ingest.repository import (
    ChunkRecord,
    DocumentRecord,
    insert_document_with_chunks,
)
from archiviste_workers.retrieve.schemas import RetrievedChunk
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

# Chunk body text used in redaction test — must not appear in emitted reason.
_CHUNK_BODY = "L'Archiviste est mort."


class _FakeJudgeLlm:
    """Configurable verdict+reason replies for each invoke call."""

    def __init__(self, replies: list[str], *, raise_all: bool = False) -> None:
        self._replies = deque(replies)
        self._raise_all = raise_all
        self.calls = 0
        self.model = "fake"
        self.provider = "fake"

    async def invoke(self, messages: list[Any], *, timeout_s: float | None = None) -> AIMessage:
        self.calls += 1
        if self._raise_all:
            raise LlmTimeoutError("boom")
        return AIMessage(content=self._replies.popleft())


# ---------------------------------------------------------------------------
# parse_verdict unit tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reply", "expected_verdict", "has_reason"),
    [
        ("PRESENT\nLe fait est présent dans lore/a.md.", "present", True),
        ("ABSENT\nBlowen n'est pas mentionné.", "absent", True),
        ("CONTRADICTION\nLes sources sont en désaccord.", "contradiction", True),
        ("UNCLEAR\n", "unclear", False),
        ("unclear", "unclear", False),
        ("present some extra text", "present", True),
        ("ABSENT", "absent", False),
        # Unknown token → unclear (fail-safe).
        ("MAYBE something", "unclear", True),
        ("", "unclear", False),
        ("Je ne sais pas", "unclear", True),
        # Old CONTRADICTION-only token still maps correctly.
        ("CONTRADICTION", "contradiction", False),
        # NO_CONTRADICTION (old token): full token → unknown → unclear; no remainder → no reason.
        ("NO_CONTRADICTION", "unclear", False),
    ],
)
def test_parse_verdict(reply: str, expected_verdict: str, has_reason: bool) -> None:
    # AC: parse_verdict returns (Verdict, reason); unknown/empty → unclear fail-safe.
    verdict, reason = parse_verdict(reply)
    assert verdict == expected_verdict
    if has_reason:
        assert len(reason) > 0
    else:
        assert reason == ""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Vote threshold / ticket policy (design decisions #4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("replies", "expected_verdict", "should_raise_ticket"),
    [
        # >=2 present → no ticket (AC: present >=2 → not_raised).
        (
            ["PRESENT\nFait confirmé.", "PRESENT\nFait confirmé.", "UNCLEAR\n"],
            "present",
            False,
        ),
        # >=2 absent → ticket (AC: absent >=2 → ticket raised).
        (
            ["ABSENT\nBlowen absent.", "ABSENT\nBlowen absent.", "UNCLEAR\n"],
            "absent",
            True,
        ),
        # >=2 contradiction → ticket (AC: contradiction >=2 → ticket raised).
        (
            [
                "CONTRADICTION\nSources en désaccord.",
                "CONTRADICTION\nSources en désaccord.",
                "PRESENT\nFait présent.",
            ],
            "contradiction",
            True,
        ),
        # >=2 unclear → ticket (AC: unclear >=2 → ticket raised).
        (["UNCLEAR\n", "UNCLEAR\n", "PRESENT\nFait présent."], "unclear", True),
        # Split vote (1 each for 3 different verdicts) → unclear, no ticket.
        (
            [
                "PRESENT\nFait présent.",
                "ABSENT\nAbsent.",
                "CONTRADICTION\nContradiction.",
            ],
            "unclear",
            False,
        ),
    ],
)
@pytest.mark.usefixtures("_stub_sources")
async def test_verdict_vote_threshold(
    ticket_spy: list[dict[str, str]],
    replies: list[str],
    expected_verdict: str,
    should_raise_ticket: bool,
) -> None:
    # AC: winning verdict = plurality >=2; any non-present >=2 raises ticket.
    result = await _run(_FakeJudgeLlm(replies))

    assert result.verdict == expected_verdict
    if should_raise_ticket:
        assert len(ticket_spy) == 1
        assert ticket_spy[0]["question"] == CLAIM
        assert ticket_spy[0]["conversation_id"] == CONV_ID
        assert result.ticket_action == "created"
        assert result.ticket_id == "ticket-1"
    else:
        assert ticket_spy == []
        assert result.ticket_action == "not_raised"
        assert result.ticket_id is None


@pytest.mark.asyncio
@pytest.mark.usefixtures("_stub_sources")
async def test_judge_failure_counts_as_unclear(ticket_spy: list[dict[str, str]]) -> None:
    # AC: failed judge → unclear (fail-safe); 3 unclear >=2 → ticket raised.
    result = await _run(_FakeJudgeLlm([], raise_all=True))

    assert result.verdict == "unclear"
    # 3 unclear votes → >=2 → ticket raised.
    assert len(ticket_spy) == 1


# ---------------------------------------------------------------------------
# No-sources path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_visible_sources_skips_judges(
    monkeypatch: pytest.MonkeyPatch, ticket_spy: list[dict[str, str]]
) -> None:
    async def _empty(pool: Any, citations: Any, user_tier: str) -> list[tuple[str, int, str]]:
        return []

    monkeypatch.setattr(service_module, "resolve_cited_sources", _empty)
    llm = _FakeJudgeLlm(["CONTRADICTION\nX.", "CONTRADICTION\nX.", "CONTRADICTION\nX."])

    result = await _run(llm)

    assert result.verdict == "unclear"
    assert llm.calls == 0
    assert ticket_spy == []


# ---------------------------------------------------------------------------
# No-citation retrieval path (design decision #5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_citation_retrieval_path(
    monkeypatch: pytest.MonkeyPatch, ticket_spy: list[dict[str, str]]
) -> None:
    """AC: when no citations given, embed claim + search, ACL-filter, run judges."""
    retrieved = [
        RetrievedChunk(
            chunk_id=str(uuid.uuid4()),
            document_id=str(uuid.uuid4()),
            source_path="lore/retrieved.md",
            ord=0,
            text=_CHUNK_BODY,
            score=0.9,
            access_tier="public",
        )
    ]

    async def _fake_search(
        pool: Any, embedding: Any, allowed_tiers: Any, top_k: int
    ) -> list[RetrievedChunk]:
        return retrieved

    monkeypatch.setattr(service_module, "search", _fake_search)

    llm = _FakeJudgeLlm(
        [
            "ABSENT\nBlowen absent de lore/retrieved.md.",
            "ABSENT\nBlowen absent de lore/retrieved.md.",
            "UNCLEAR\n",
        ]
    )

    result = await verify_contradiction(
        pool=object(),
        embedder=_embedder,
        llm=llm,
        claim="Blowen n'est pas un personnage du lore.",
        conversation_id=CONV_ID,
        citations=[],  # no citations → retrieval path
        user_tier="anonymous",
        request_id=REQUEST_ID,
    )

    assert result.verdict == "absent"
    assert llm.calls == 3
    assert len(ticket_spy) == 1


# ---------------------------------------------------------------------------
# Redaction assertion (design decision #7)
# ---------------------------------------------------------------------------

# A chunk body long enough to exceed _MIN_LEAK_SUBSTR_CHARS (24).
_LONG_CHUNK_BODY = "L'Archiviste est mort en l'an zéro selon les archives."


@pytest.fixture
def _stub_sources_with_long_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub resolve_cited_sources to return a chunk whose body is >= MIN_LEAK_SUBSTR_CHARS."""

    async def _fake_resolve(
        pool: Any, citations: Any, user_tier: str
    ) -> list[tuple[str, int, str]]:
        return [("lore/a.md", 0, _LONG_CHUNK_BODY)]

    monkeypatch.setattr(service_module, "resolve_cited_sources", _fake_resolve)


@pytest.mark.asyncio
@pytest.mark.usefixtures("_stub_sources_with_long_body")
async def test_reason_containing_chunk_body_is_redacted(ticket_spy: list[dict[str, str]]) -> None:
    """AC: structural redaction (#162) — if LLM embeds chunk body in reason, it is replaced.

    This test FAILS against pre-fix code (no _redact_reason call) because the verbatim
    chunk body leaks through.  It PASSES after the fix because _redact_reason substitutes
    the safe generic reason.
    """
    # Judge deliberately copies a substring of _LONG_CHUNK_BODY into its reason.
    leak_fragment = _LONG_CHUNK_BODY  # full body — well above MIN_LEAK_SUBSTR_CHARS
    llm = _FakeJudgeLlm(
        [
            f"CONTRADICTION\n{leak_fragment}",
            f"CONTRADICTION\n{leak_fragment}",
            "UNCLEAR\n",
        ]
    )
    result = await _run(llm)

    assert result.verdict == "contradiction"
    # Structural guarantee: chunk body must NOT appear in emitted reason.
    assert _LONG_CHUNK_BODY not in result.reason
    # The replacement is the safe generic (not an empty string).
    assert len(result.reason) > 0


@pytest.mark.asyncio
@pytest.mark.usefixtures("_stub_sources")
async def test_clean_reason_passes_through_unchanged(ticket_spy: list[dict[str, str]]) -> None:
    """AC: a judge reason that does not copy any chunk body is returned verbatim."""
    # parse_verdict strips leading/trailing " .:-\n" so we compare the trimmed form.
    clean_reason_raw = "Source lore/a.md en désaccord avec lore/b.md."
    clean_reason_trimmed = clean_reason_raw.strip(" .:-\n")
    llm = _FakeJudgeLlm(
        [
            f"CONTRADICTION\n{clean_reason_raw}",
            f"CONTRADICTION\n{clean_reason_raw}",
            "UNCLEAR\n",
        ]
    )
    result = await _run(llm)

    assert result.verdict == "contradiction"
    # _CHUNK_BODY ("L'Archiviste est mort.") is only 22 chars — below MIN_LEAK_SUBSTR_CHARS (24),
    # so the short stub sources do not trigger redaction and the clean reason passes through.
    assert result.reason == clean_reason_trimmed


# ---------------------------------------------------------------------------
# DB-backed tests
# ---------------------------------------------------------------------------


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
    # AC: cited chunks above caller tier are dropped (ACL re-resolution).
    await _seed_chunk(clean_db, "lore/public.md", "public", "Texte public.")
    await _seed_chunk(clean_db, "lore/secret.md", "author_only", "Texte secret.")
    citations = [
        Citation(source_path="lore/public.md", chunk_ords=[0]),
        Citation(source_path="lore/secret.md", chunk_ords=[0]),
    ]

    anon = await resolve_cited_sources(clean_db, citations, "anonymous")
    author = await resolve_cited_sources(clean_db, citations, "author_only")

    assert anon == [("lore/public.md", 0, "Texte public.")]
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
        llm=_FakeJudgeLlm(
            [
                "CONTRADICTION\nSources contredisent.",
                "CONTRADICTION\nSources contredisent.",
                "CONTRADICTION\nSources contredisent.",
            ]
        ),
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

    assert first.verdict == "contradiction"
    assert first.ticket_action == "created"
    assert first.ticket_id is not None
    assert second.ticket_action == "incremented"
    assert second.ticket_id == first.ticket_id
    async with clean_db.acquire() as conn:
        await conn.execute("DELETE FROM tickets")


@pytest.mark.asyncio
async def test_confirmed_unknown_conversation_yields_skipped_error(clean_db: asyncpg.Pool) -> None:
    # FK miss: confirmed, but conversation_id absent → tickets FK RESTRICT → skipped_error.
    await _seed_chunk(clean_db, "lore/x.md", "public", "L'Archiviste est mort.")

    result = await _confirmed_verify(clean_db, "55555555-5555-4555-8555-555555555555")

    assert result.verdict == "contradiction"
    assert result.ticket_action == "skipped_error"
    assert result.ticket_id is None


# ---------------------------------------------------------------------------
# HTTP contract / validation tests
# ---------------------------------------------------------------------------


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
    # AC: validation errors map to the contract error enum.
    body = _valid_body()
    mutate(body)
    request_headers = {"x-user-id": USER_ID, "x-user-tier": "anonymous"} if headers else {}

    resp = await verify_client.post("/v1/verify-contradiction", json=body, headers=request_headers)

    assert resp.status_code == 400
    assert resp.json() == {"error": expected_error}


def test_empty_citations_accepted_by_model() -> None:
    """AC: citations is optional — empty array and absent field are both valid."""
    # AC: no-citation path — VerifyContradictionRequest accepts empty citations.
    req_with_empty = VerifyContradictionRequest.model_validate(
        {
            "claim": CLAIM,
            "conversation_id": CONV_ID,
            "citations": [],
            "request_id": REQUEST_ID,
        }
    )
    assert req_with_empty.citations == []

    req_without = VerifyContradictionRequest.model_validate(
        {
            "claim": CLAIM,
            "conversation_id": CONV_ID,
            "request_id": REQUEST_ID,
        }
    )
    assert req_without.citations == []
