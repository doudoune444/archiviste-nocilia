"""Contradiction/lore-gap verification — 4-way verdict panel + ticket policy (#162).

Three independent judges return PRESENT/ABSENT/CONTRADICTION/UNCLEAR + a reason.
Winning verdict = plurality >=2.  Any non-present >=2 winner raises a ticket.
No citations → embed the claim and retrieve top-5 chunks as sources.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Final

import asyncpg
import structlog

from archiviste_workers.contradiction.models import (
    RETRIEVAL_TOP_K,
    TICKET_TRIGGERING_VERDICTS,
    Citation,
    TicketAction,
    Verdict,
)
from archiviste_workers.contradiction.prompt import (
    JUDGE_LENSES,
    build_judge_messages,
    parse_verdict,
)
from archiviste_workers.contradiction.repository import (
    SourceResolutionError,
    resolve_cited_sources,
)
from archiviste_workers.retrieve.search import DatabaseUnavailableError, search
from archiviste_workers.services.acl import ALLOWED_ACCESS_TIERS_BY_USER_TIER
from archiviste_workers.services.llm import (
    LlmClientProtocol,
    LlmTimeoutError,
    LlmUpstreamError,
)
from archiviste_workers.services.ticket_service import TicketResult, create_or_increment

logger = structlog.get_logger()

JUDGE_TIMEOUT_SECONDS: Final = 30.0
# A verdict must receive at least this many votes to win (and potentially raise a ticket).
MAJORITY_THRESHOLD: Final = 2

# Synthesized reason when judges disagree and no verdict reaches >=2 votes.
_REASON_UNCLEAR: Final = "Les juges n'ont pas pu trancher l'affirmation."
# Synthesized reason when no sources are available.
_REASON_NO_SOURCES: Final = "Aucune source disponible pour évaluer l'affirmation."


@dataclass(frozen=True)
class VerificationResult:
    verdict: Verdict
    reason: str
    ticket_action: TicketAction
    ticket_id: str | None


async def _run_judge(
    llm: LlmClientProtocol,
    claim: str,
    sources: list[tuple[str, int, str]],
    lens: str,
    request_id: str,
) -> tuple[Verdict, str]:
    """One four-way judge.  A judge that cannot answer returns unclear (fail-safe)."""
    try:
        reply = await llm.invoke(
            build_judge_messages(claim, sources, lens), timeout_s=JUDGE_TIMEOUT_SECONDS
        )
    except (LlmTimeoutError, LlmUpstreamError) as exc:
        logger.warning(
            "contradiction_judge_failed", request_id=request_id, error_type=type(exc).__name__
        )
        return "unclear", ""
    content = reply.content if isinstance(reply.content, str) else str(reply.content)
    return parse_verdict(content)


def _aggregate_verdicts(
    outcomes: list[tuple[Verdict, str]],
) -> tuple[Verdict, str, bool]:
    """Tally three judge verdicts; return (verdict, reason, has_majority).

    has_majority=True means some verdict reached >=2 votes (design decision #4).
    When no verdict reaches >=2 the aggregate verdict is unclear but no ticket is raised.
    Reason references only verdict + source_path identifiers (design decision #7).

    Priority: contradiction > absent > present > unclear (most actionable first).
    """
    tally: dict[str, list[str]] = {
        "present": [],
        "absent": [],
        "contradiction": [],
        "unclear": [],
    }
    for verdict, reason in outcomes:
        bucket = tally.get(verdict)
        if bucket is not None:
            bucket.append(reason)

    priority: tuple[Verdict, ...] = ("contradiction", "absent", "present", "unclear")
    for candidate in priority:
        reasons = tally[candidate]
        if len(reasons) >= MAJORITY_THRESHOLD:
            synthesized = next((r for r in reasons if r), _REASON_UNCLEAR)
            return candidate, synthesized, True

    # No verdict reached >=2 — no majority, no ticket (design decision #4).
    return "unclear", _REASON_UNCLEAR, False


async def _resolve_sources(
    pool: asyncpg.Pool,
    embedder: Any,
    citations: list[Citation],
    user_tier: str,
    claim: str,
    request_id: str,
) -> list[tuple[str, int, str]] | None:
    """Return sources or None on unrecoverable failure.

    When citations are absent/empty, embed claim + retrieve top-k, then ACL-filter.
    """
    if citations:
        try:
            return await resolve_cited_sources(pool, citations, user_tier)
        except SourceResolutionError:
            return None

    # No-citation retrieval path (design decision #5).
    allowed_tiers = list(ALLOWED_ACCESS_TIERS_BY_USER_TIER.get(user_tier, frozenset()))
    try:
        embedding = embedder.encode_batch([claim], batch_size=1)[0]
    except (RuntimeError, ValueError) as exc:
        logger.warning(
            "contradiction_embed_failed",
            request_id=request_id,
            error_type=type(exc).__name__,
        )
        return None

    try:
        chunks = await search(pool, embedding, allowed_tiers, RETRIEVAL_TOP_K)
    except DatabaseUnavailableError as exc:
        logger.warning(
            "contradiction_retrieval_failed",
            request_id=request_id,
            error_type=type(exc).__name__,
        )
        return None

    # ACL-filter retrieved chunks by user tier (design decision #5).
    return [
        (chunk.source_path, chunk.ord, chunk.text)
        for chunk in chunks
        if chunk.access_tier in ALLOWED_ACCESS_TIERS_BY_USER_TIER.get(user_tier, frozenset())
    ]


async def verify_contradiction(
    *,
    pool: asyncpg.Pool,
    embedder: Any,
    llm: LlmClientProtocol,
    claim: str,
    conversation_id: str,
    citations: list[Citation],
    user_tier: str,
    request_id: str,
) -> VerificationResult:
    """Verify a claim; raise a lore-gap ticket when the panel verdict is non-present >=2."""
    sources = await _resolve_sources(pool, embedder, citations, user_tier, claim, request_id)

    if sources is None:
        return VerificationResult("unclear", _REASON_NO_SOURCES, "not_raised", None)

    if not sources:
        logger.info(
            "contradiction_no_sources",
            request_id=request_id,
            conversation_id=conversation_id,
        )
        return VerificationResult("unclear", _REASON_NO_SOURCES, "not_raised", None)

    raw_outcomes: list[tuple[Verdict, str]] = list(
        await asyncio.gather(
            *(_run_judge(llm, claim, sources, lens, request_id) for lens in JUDGE_LENSES)
        )
    )

    # Log per-judge reasons server-side for ops — NOT returned to caller (design decision #6).
    for idx, (verdict, reason) in enumerate(raw_outcomes):
        logger.info(
            "contradiction_judge_outcome",
            request_id=request_id,
            judge_index=idx,
            verdict=verdict,
            reason=reason,
        )

    winning_verdict, synthesized_reason, has_majority = _aggregate_verdicts(raw_outcomes)

    should_raise = has_majority and winning_verdict in TICKET_TRIGGERING_VERDICTS
    if not should_raise:
        logger.info(
            "contradiction_no_ticket",
            request_id=request_id,
            conversation_id=conversation_id,
            verdict=winning_verdict,
            has_majority=has_majority,
        )
        return VerificationResult(winning_verdict, synthesized_reason, "not_raised", None)

    ticket: TicketResult = await create_or_increment(
        pool,
        embedder,
        conversation_id=conversation_id,
        question=claim,
        request_id=request_id,
    )
    logger.info(
        "contradiction_ticket_raised",
        request_id=request_id,
        conversation_id=conversation_id,
        verdict=winning_verdict,
        ticket_action=ticket.action,
    )
    return VerificationResult(winning_verdict, synthesized_reason, ticket.action, ticket.ticket_id)
