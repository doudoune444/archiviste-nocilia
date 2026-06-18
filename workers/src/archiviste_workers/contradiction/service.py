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
    Outcome,
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
# Safe generic reasons per verdict — used when structural redaction detects a chunk-body leak.
# Structural guarantee (#162): the emitted reason never contains retrieved chunk body text,
# regardless of LLM behavior.  Prompt-level instruction is defense-in-depth only.
_SAFE_REASON_BY_VERDICT: Final[dict[str, str]] = {
    "present": "L'affirmation est soutenue par les sources.",
    "absent": "L'information est absente des sources.",
    "contradiction": "Les sources se contredisent.",
    "unclear": _REASON_UNCLEAR,
}
# Minimum substring length that triggers redaction.  Short substrings (< this many chars)
# are too common to be reliable leak signals (e.g. single words).
_MIN_LEAK_SUBSTR_CHARS: Final = 24


@dataclass(frozen=True)
class VerificationResult:
    verdict: Verdict
    reason: str
    ticket_action: TicketAction
    ticket_id: str | None
    outcome: Outcome


def _derive_outcome(verdict: Verdict, should_raise: bool) -> Outcome:
    """Pure function: map judge result to typed outcome (#172).

    confirmed iff should_raise (judge-confirmed absent/contradiction majority).
    refused iff verdict == present (lore is consistent).
    indecisive otherwise (unclear, no-majority, no-sources, or force path).
    A force=True ticket is the untrusted judges_not_passed path — never confirmed.
    """
    if should_raise:
        return "confirmed"
    if verdict == "present":
        return "refused"
    return "indecisive"


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


def _redact_reason(
    reason: str,
    verdict: str,
    sources: list[tuple[str, int, str]],
) -> str:
    """Return reason unchanged, or a safe generic if it contains any source body text.

    Structural guarantee (#162): emitted reason never leaks raw chunk body text regardless
    of LLM behavior — the prompt instruction is defense-in-depth only.
    Check: for each source body, if a normalized substring of length >= _MIN_LEAK_SUBSTR_CHARS
    appears in the reason, treat as leak and substitute the per-verdict safe generic.
    """
    reason_lower = reason.lower()
    for _path, _ord, body in sources:
        if len(body) < _MIN_LEAK_SUBSTR_CHARS:
            continue
        body_lower = body.lower()
        # Slide over the body in steps of _MIN_LEAK_SUBSTR_CHARS.
        step = _MIN_LEAK_SUBSTR_CHARS
        for start in range(0, len(body_lower) - step + 1, step):
            substr = body_lower[start : start + step]
            if substr in reason_lower:
                logger.warning(
                    "contradiction_reason_redacted",
                    verdict=verdict,
                    leak_substr_start=start,
                )
                return _SAFE_REASON_BY_VERDICT.get(verdict, _REASON_UNCLEAR)
    return reason


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
    # Bind once — reused for both the search call and the post-retrieval ACL filter.
    allowed_tiers_set = ALLOWED_ACCESS_TIERS_BY_USER_TIER.get(user_tier, frozenset())
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
        chunks = await search(pool, embedding, list(allowed_tiers_set), RETRIEVAL_TOP_K)
    except DatabaseUnavailableError as exc:
        logger.warning(
            "contradiction_retrieval_failed",
            request_id=request_id,
            error_type=type(exc).__name__,
        )
        return None

    # ACL-filter retrieved chunks by user tier (design decision #5).
    # Unknown chunk tier is fail-closed (security.md A01): chunk is silently dropped with telemetry.
    result = []
    for chunk in chunks:
        if chunk.access_tier not in {"public", "members", "author_only"}:
            logger.error("acl_unknown_tier", chunk_id=chunk.source_path, request_id=request_id)
            continue
        if chunk.access_tier in allowed_tiers_set:
            result.append((chunk.source_path, chunk.ord, chunk.text))
    return result


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
    force: bool = False,
) -> VerificationResult:
    """Verify a claim; raise a lore-gap ticket when the panel verdict is non-present >=2.

    When force=True and the judges did NOT confirm (should_raise is False), a ticket is
    still raised — tagged judges_not_passed=True so the board can distinguish it (#163).
    Cosine dedup applies on the judge-confirmed path; force=True bypasses dedup and always
    inserts a new ticket so an unconfirmed override is never hidden behind a confirmed one.
    """
    sources = await _resolve_sources(pool, embedder, citations, user_tier, claim, request_id)

    if sources is None:
        return VerificationResult("unclear", _REASON_NO_SOURCES, "not_raised", None, "indecisive")

    if not sources:
        logger.info(
            "contradiction_no_sources",
            request_id=request_id,
            conversation_id=conversation_id,
        )
        return VerificationResult("unclear", _REASON_NO_SOURCES, "not_raised", None, "indecisive")

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

    # Structural guarantee (#162): redact any chunk body text from the reason before emitting.
    safe_reason = _redact_reason(synthesized_reason, winning_verdict, sources)

    should_raise = has_majority and winning_verdict in TICKET_TRIGGERING_VERDICTS
    if should_raise:
        ticket: TicketResult = await create_or_increment(
            pool,
            embedder,
            conversation_id=conversation_id,
            question=claim,
            request_id=request_id,
            judges_not_passed=False,
        )
        outcome = _derive_outcome(winning_verdict, should_raise=True)
        logger.info(
            "contradiction_ticket_raised",
            request_id=request_id,
            conversation_id=conversation_id,
            verdict=winning_verdict,
            ticket_action=ticket.action,
            outcome=outcome,
        )
        return VerificationResult(
            winning_verdict, safe_reason, ticket.action, ticket.ticket_id, outcome
        )

    if force:
        # Human override: judges did not confirm, but the visitor insists (#163/#175).
        # Dedup is bypassed — force always inserts a new ticket so the override
        # appears on the board with its own "non confirmé par les juges" badge (#175).
        # outcome is derived from the underlying verdict, never "confirmed" (#172).
        ticket = await create_or_increment(
            pool,
            embedder,
            conversation_id=conversation_id,
            question=claim,
            request_id=request_id,
            judges_not_passed=True,
            force=True,
        )
        outcome = _derive_outcome(winning_verdict, should_raise=False)
        logger.info(
            "contradiction_ticket_forced",
            request_id=request_id,
            conversation_id=conversation_id,
            verdict=winning_verdict,
            ticket_action=ticket.action,
            outcome=outcome,
        )
        return VerificationResult(
            winning_verdict, safe_reason, ticket.action, ticket.ticket_id, outcome
        )

    outcome = _derive_outcome(winning_verdict, should_raise=False)
    logger.info(
        "contradiction_no_ticket",
        request_id=request_id,
        conversation_id=conversation_id,
        verdict=winning_verdict,
        has_majority=has_majority,
        outcome=outcome,
    )
    return VerificationResult(winning_verdict, safe_reason, "not_raised", None, outcome)
