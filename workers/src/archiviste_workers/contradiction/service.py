"""Contradiction verification — 3 refute-biased judges + lore-gap ticket reuse (CTR-001).

Resolve cited sources (ACL-bounded), run three independent judges concurrently, and
on >=2 confirmations raise/increment a lore-gap ticket carrying the visitor's claim.
No new ticket type. Every failure mode is fail-safe: it never raises a bogus ticket.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Final

import asyncpg
import structlog

from archiviste_workers.contradiction.models import Citation, TicketAction
from archiviste_workers.contradiction.prompt import (
    JUDGE_LENSES,
    build_judge_messages,
    is_confirmation,
)
from archiviste_workers.contradiction.repository import (
    SourceResolutionError,
    resolve_cited_sources,
)
from archiviste_workers.services.llm import (
    LlmClientProtocol,
    LlmTimeoutError,
    LlmUpstreamError,
)
from archiviste_workers.services.ticket_service import TicketResult, create_or_increment

logger = structlog.get_logger()

CONFIRMATION_THRESHOLD: Final = 2
JUDGE_TIMEOUT_SECONDS: Final = 30.0


@dataclass(frozen=True)
class VerificationResult:
    contradiction_confirmed: bool
    confirmations: int
    ticket_action: TicketAction
    ticket_id: str | None


async def _run_judge(
    llm: LlmClientProtocol,
    claim: str,
    sources: list[tuple[str, int, str]],
    lens: str,
    request_id: str,
) -> bool:
    """One refute-biased judge. A judge that cannot answer does not confirm."""
    try:
        reply = await llm.invoke(
            build_judge_messages(claim, sources, lens), timeout_s=JUDGE_TIMEOUT_SECONDS
        )
    except (LlmTimeoutError, LlmUpstreamError) as exc:
        logger.warning(
            "contradiction_judge_failed", request_id=request_id, error_type=type(exc).__name__
        )
        return False
    content = reply.content if isinstance(reply.content, str) else str(reply.content)
    return is_confirmation(content)


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
    """Verify a visitor's contradiction report; raise a lore-gap ticket on >=2 confirmations."""
    try:
        sources = await resolve_cited_sources(pool, citations, user_tier)
    except SourceResolutionError:
        return VerificationResult(False, 0, "not_raised", None)

    if not sources:
        logger.info(
            "contradiction_no_sources", request_id=request_id, conversation_id=conversation_id
        )
        return VerificationResult(False, 0, "not_raised", None)

    votes = await asyncio.gather(
        *(_run_judge(llm, claim, sources, lens, request_id) for lens in JUDGE_LENSES)
    )
    confirmations = sum(1 for vote in votes if vote)
    if confirmations < CONFIRMATION_THRESHOLD:
        logger.info(
            "contradiction_unconfirmed",
            request_id=request_id,
            conversation_id=conversation_id,
            confirmations=confirmations,
        )
        return VerificationResult(False, confirmations, "not_raised", None)

    ticket: TicketResult = await create_or_increment(
        pool,
        embedder,
        conversation_id=conversation_id,
        question=claim,
        request_id=request_id,
    )
    logger.info(
        "contradiction_confirmed",
        request_id=request_id,
        conversation_id=conversation_id,
        confirmations=confirmations,
        ticket_action=ticket.action,
    )
    return VerificationResult(True, confirmations, ticket.action, ticket.ticket_id)
