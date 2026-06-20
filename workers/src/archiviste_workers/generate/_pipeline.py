"""Shared finalize pipeline used by both router.py (blocking) and stream_router.py (SSE).

TECH-230: extracts the per-mode persistence logic (conversation append + query_log success row
+ lore-gap ticket + INFO log) into a single module so future changes touch one place.

The mode-decision / message-building step remains in each router because tests patch the prompt
functions through the router module's namespace (patch.object(router_module, "build_messages")),
which requires the call site to live there.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

import structlog

from archiviste_workers.generate.models import (
    Citation,
    GenerateRequest,
    Mode,
    Usage,
)
from archiviste_workers.services.intent import IntentResult
from archiviste_workers.services.query_log import QueryLogRow
from archiviste_workers.services.ticket_service import TicketResult, create_or_increment

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Context protocol — both _RequestContext and _StreamContext satisfy this.
# ---------------------------------------------------------------------------


class PipelineCtx(Protocol):
    """Minimal interface required by finalize_generation.

    Both _RequestContext (router.py) and _StreamContext (stream_router.py) implement
    this protocol structurally — no explicit inheritance needed.
    """

    @property
    def parsed(self) -> GenerateRequest: ...

    @property
    def conversation_id(self) -> str: ...

    @property
    def conversation_client(self) -> Any: ...

    @property
    def query_log_repo(self) -> Any: ...

    @property
    def db_pool(self) -> Any: ...

    @property
    def embedder(self) -> Any: ...

    @property
    def started(self) -> float: ...


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PreparedMode:
    """Mode decision + messages, built by each router before the LLM call.

    Carries intent_result so finalize_generation needs no extra parameter.
    The message-building functions (build_messages, etc.) are called in each
    router's namespace to preserve test patchability.
    """

    mode: Mode
    messages: list[Any]
    # canon only: visible chunks for citation extraction and log metadata.
    visible_chunks: list[Any]  # list[Chunk] — typed Any to avoid re-import in callers
    retrieve_ms: int
    # lore_gap only: max cosine score for INFO log.
    max_score: float
    # mystery / canon-with-blocked: ACL-blocked chunk count.
    blocked_count: int
    # Carried through from classify_intent so finalize_generation needs no extra param.
    intent_result: IntentResult


@dataclass(frozen=True)
class GenerationResult:
    """Caller-computed result after the LLM call."""

    answer: str
    usage: Usage
    # Empty for off_topic / lore_gap / mystery.
    citations: list[Citation]
    llm_ms: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def add_optional(a: int | None, b: int | None) -> int | None:
    if a is None or b is None:
        return None
    return a + b


def add_decimal_optional(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    if a is None or b is None:
        return None
    return a + b


# ---------------------------------------------------------------------------
# Finalize — single source of truth for persist + query_log + ticket + INFO log.
# ---------------------------------------------------------------------------


async def finalize_generation(
    ctx: PipelineCtx,
    prepared: PreparedMode,
    result: GenerationResult,
    *,
    log_event: str,
    ticket_creator: Any = None,
) -> str:
    """Persist conversation turn, insert success query_log row, gate lore-gap ticket, emit INFO.

    Returns the ticket_action string ("created" | "incremented" | "skipped_error" | "skipped").
    log_event must be "generate" (blocking path) or "generate_stream" (SSE path).

    ticket_creator: callable matching create_or_increment's signature. Defaults to the
    imported create_or_increment. Pass an explicit reference from the calling module when
    tests need to patch it via patch.object(calling_module, "create_or_increment", ...).
    """
    parsed = ctx.parsed
    now = datetime.now(UTC)

    user_append = await ctx.conversation_client.append_message(
        conversation_id=ctx.conversation_id,
        role="user",
        content=parsed.query,
        timestamp=now,
        user_id=parsed.user_id,
    )
    asst_append = await ctx.conversation_client.append_message(
        conversation_id=ctx.conversation_id,
        role="assistant",
        content=result.answer,
        timestamp=datetime.now(UTC),
        user_id=parsed.user_id,
    )

    effective_ticket_creator = ticket_creator if ticket_creator is not None else create_or_increment
    ticket_action = await _maybe_create_ticket(
        ctx, prepared, user_append, asst_append, effective_ticket_creator
    )

    latency_ms = int((time.perf_counter() - ctx.started) * 1000)
    intent_val = "off_topic" if prepared.mode == "off_topic" else prepared.intent_result.intent
    await ctx.query_log_repo.insert(
        QueryLogRow(
            request_id=parsed.request_id,
            user_id=parsed.user_id,
            conversation_id=ctx.conversation_id,
            query_text=parsed.query,
            mode=prepared.mode,
            intent=intent_val,
            status_code=200,
            latency_ms=latency_ms,
            prompt_tokens=result.usage.prompt_tokens,
            completion_tokens=result.usage.completion_tokens,
            cost_eur=result.usage.cost_eur,
        )
    )

    _log_info(ctx, prepared, result, ticket_action, log_event=log_event)
    return ticket_action


async def _maybe_create_ticket(
    ctx: PipelineCtx,
    prepared: PreparedMode,
    user_append: Any,
    asst_append: Any,
    ticket_creator: Any,
) -> str:
    """Create or increment a lore-gap ticket when conditions are met (AC-9/AC-12).

    ticket_creator is the create_or_increment callable (may be a patched version from
    the calling module's namespace for test observability).
    """
    if prepared.mode != "lore_gap":
        return "skipped"

    conversation_logged = user_append.ok and asst_append.ok
    if not conversation_logged:
        logger.warning(
            "ticket_service_skipped_after_conversation_log_failed",
            request_id=ctx.parsed.request_id,
        )
        return "skipped_error"

    if ctx.db_pool is None or ctx.embedder is None:
        return "skipped_error"

    ticket_result: TicketResult = await ticket_creator(
        ctx.db_pool,
        ctx.embedder,
        conversation_id=ctx.conversation_id,
        # AC-12: store raw query (no prefix) in tickets.question.
        question=ctx.parsed.query,
        request_id=ctx.parsed.request_id,
    )
    return ticket_result.action


def _log_info(
    ctx: PipelineCtx,
    prepared: PreparedMode,
    result: GenerationResult,
    ticket_action: str,
    *,
    log_event: str,
) -> None:
    """Emit the per-mode INFO log line."""
    parsed = ctx.parsed
    intent_result = prepared.intent_result
    mode = prepared.mode
    intent_val = "off_topic" if mode == "off_topic" else intent_result.intent

    base_kwargs: dict[str, Any] = {
        "request_id": parsed.request_id,
        "conversation_id": ctx.conversation_id,
        "intent": intent_val,
        "mode": mode,
        "status": 200,
        "retrieve_ms": prepared.retrieve_ms,
        "llm_ms": result.llm_ms,
        "chunks": _chunk_count(prepared),
        "citations": len(result.citations),
    }

    if log_event == "generate_stream":
        base_kwargs["query_len"] = len(parsed.query)

    if mode == "lore_gap":
        base_kwargs["top_score"] = round(prepared.max_score, 4)
        base_kwargs["ticket_action"] = ticket_action
    elif mode in ("mystery", "canon") and prepared.blocked_count > 0:
        base_kwargs["blocked_count"] = prepared.blocked_count

    logger.info(log_event, **base_kwargs)


def _chunk_count(prepared: PreparedMode) -> int:
    """Return the value logged as `chunks=` for each mode (mirrors original per-mode logic)."""
    if prepared.mode == "mystery":
        # AC-18: blocked_count is logged as chunks for mystery.
        return prepared.blocked_count
    # canon: visible chunk count; lore_gap: retrieved chunk count; off_topic: 0.
    return len(prepared.visible_chunks)
