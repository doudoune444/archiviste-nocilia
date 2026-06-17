"""POST /v1/generate — Modes 1-4 (canon, off_topic, lore_gap, mystery). Internal-only."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from langchain_core.messages import BaseMessage
from pydantic import ValidationError

from archiviste_workers.generate.injection_filter import detect_injection
from archiviste_workers.generate.memory import load_memory_window
from archiviste_workers.generate.models import (
    LORE_GAP_THRESHOLD,
    GenerateRequest,
    GenerateResponse,
    Usage,
    is_valid_uuid,
)
from archiviste_workers.generate.parser import extract_citations
from archiviste_workers.generate.pricing import compute_cost_eur
from archiviste_workers.generate.prompt import (
    build_lore_gap_messages,
    build_messages,
    build_mystery_messages,
    build_off_topic_messages,
)
from archiviste_workers.services.acl import filter_chunks_by_tier
from archiviste_workers.services.conversation_client import ConversationClient
from archiviste_workers.services.intent import IntentResult, classify_intent
from archiviste_workers.services.llm import (
    LlmClient,
    LlmTimeoutError,
    LlmUpstreamError,
    extract_usage,
)
from archiviste_workers.services.query_log import QueryLogRepository, QueryLogRow
from archiviste_workers.services.retrieve_client import RetrieveClient, RetrieveError
from archiviste_workers.services.ticket_service import TicketResult, create_or_increment

router = APIRouter(prefix="/v1", tags=["generate"])
logger = structlog.get_logger()

TOP_K = 5


class _GenerateError(Exception):
    # Internal-only sentinel: caught by post_generate to emit a JSON body of exactly
    # {"error": code} (spec AC-11/17/18/19/21/22/23). Avoids FastAPI's HTTPException
    # which wraps detail under {"detail": ...}.
    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


def _error_response(status: int, code: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code})


# Contract vocabulary (specs/openapi/gateway-to-workers.yml X-User-Tier enum) mapped to the
# internal ACL tier names used by retrieve/schemas.py and services/acl.py.
# Translation happens at this boundary only — internal code keeps its own vocabulary.
_CONTRACT_TIER_TO_INTERNAL: dict[str, str] = {
    "anonymous": "anonymous",
    "member": "members",
    "author": "author_only",
}


def _parse_request(payload: dict[str, Any], headers: Any) -> GenerateRequest:
    raw_user_id = headers.get("x-user-id")
    if not raw_user_id or not is_valid_uuid(raw_user_id):
        raise _GenerateError(400, "invalid_user_id")

    raw_user_tier = headers.get("x-user-tier")
    if not raw_user_tier or raw_user_tier not in _CONTRACT_TIER_TO_INTERNAL:
        raise _GenerateError(422, "invalid_user_tier")

    payload["user_id"] = raw_user_id
    payload["user_tier"] = _CONTRACT_TIER_TO_INTERNAL[raw_user_tier]

    if "request_id" not in payload or not isinstance(payload.get("request_id"), str):
        raise _GenerateError(400, "invalid_request_id")
    if not is_valid_uuid(payload["request_id"]):
        raise _GenerateError(400, "invalid_request_id")
    if (
        payload.get("conversation_id") is not None
        and isinstance(payload["conversation_id"], str)
        and not is_valid_uuid(payload["conversation_id"])
    ):
        raise _GenerateError(400, "invalid_conversation_id")
    try:
        return GenerateRequest.model_validate(payload)
    except ValidationError as exc:
        for err in exc.errors():
            loc = err.get("loc", ())
            if loc and loc[0] == "request_id":
                raise _GenerateError(400, "invalid_request_id") from exc
            if loc and loc[0] == "query":
                raise _GenerateError(400, "invalid_query") from exc
        raise _GenerateError(400, "invalid_query") from exc


@dataclass(frozen=True)
class _RequestContext:
    parsed: GenerateRequest
    conversation_id: str
    suspected: str | None
    retrieve_client: RetrieveClient
    llm_client: LlmClient
    conversation_client: ConversationClient
    query_log_repo: QueryLogRepository
    db_pool: Any  # asyncpg.Pool — typed as Any to avoid hard import in dataclass
    embedder: Any  # Embedder | FakeEmbedder — typed as Any (protocol-compatible)
    started: float
    # MEM-002: prior turns injected into generation; context-aware query for
    # intent + retrieval embedding (last user turn prepended). Generation,
    # persistence and query_log keep the raw parsed.query.
    memory_messages: list[BaseMessage]
    retrieval_query: str


@router.post("/generate", response_model=GenerateResponse)
async def post_generate(
    request: Request, payload: dict[str, Any]
) -> GenerateResponse | JSONResponse:
    try:
        return await _handle_generate(request, payload)
    except _GenerateError as exc:
        return _error_response(exc.status, exc.code)


async def _handle_generate(request: Request, payload: dict[str, Any]) -> GenerateResponse:
    parsed = _parse_request(payload, request.headers)
    conversation_id = parsed.conversation_id or str(uuid.uuid4())
    suspected = detect_injection(parsed.query)
    if suspected:
        # AC-17: one log per request.
        logger.warning(
            "prompt_injection_suspected", request_id=parsed.request_id, pattern=suspected
        )

    # MEM-002: read the bounded recent-turns window once, before any LLM/retrieve
    # call. The read is owner-scoped on parsed.user_id, so a caller passing
    # someone else's conversation_id gets no history (no cross-conversation leak,
    # security.md A01). Read happens before this turn is persisted, so the window
    # holds only prior turns. last_user_turn makes elliptical follow-ups
    # self-contained for intent + retrieval embedding (no extra LLM call).
    settings = getattr(request.app.state, "settings", None)
    token_budget = settings.memory_token_budget if settings is not None else 0
    window = await load_memory_window(
        getattr(request.app.state, "conversation_repo", None),
        conversation_id,
        parsed.user_id,
        token_budget=token_budget,
    )
    retrieval_query = (
        f"{window.last_user_turn}\n{parsed.query}" if window.last_user_turn else parsed.query
    )

    # MEM-002: the augmented query feeds the prior user turn (DB-resident text) to
    # the classifier; re-run the injection filter on it so a poisoned history turn
    # still trips the suspected-injection prefix (parity with parsed.query).
    history_suspected = window.last_user_turn is not None and bool(
        detect_injection(window.last_user_turn)
    )

    ctx = _RequestContext(
        parsed=parsed,
        conversation_id=conversation_id,
        suspected=suspected,
        retrieve_client=request.app.state.retrieve_client,
        llm_client=request.app.state.llm_client,
        conversation_client=request.app.state.conversation_client,
        query_log_repo=request.app.state.query_log_repo,
        db_pool=getattr(request.app.state, "db_pool", None),
        embedder=getattr(request.app.state, "embedder", None),
        started=time.perf_counter(),
        memory_messages=window.messages,
        retrieval_query=retrieval_query,
    )

    # AC-2: classify intent before any retrieve/LLM call (context-aware query).
    intent_result = await classify_intent(
        llm_client=ctx.llm_client,
        query=ctx.retrieval_query,
        suspected_injection=bool(suspected) or history_suspected,
        request_id=parsed.request_id,
    )

    if intent_result.intent == "off_topic":
        return await _run_off_topic(ctx, intent_result)
    return await _run_canon(ctx, intent_result)


async def _run_canon(ctx: _RequestContext, intent_result: IntentResult) -> GenerateResponse:
    """Pipeline canon/lore_gap (Mode 1 or 3 — retrieve → score branch → LLM → log)."""
    parsed = ctx.parsed

    retrieve_started = time.perf_counter()
    try:
        chunks = await ctx.retrieve_client.search(
            query=ctx.retrieval_query,
            top_k=TOP_K,
            user_tier=parsed.user_tier,
            request_id=parsed.request_id,
        )
    except RetrieveError as exc:
        logger.error("retrieve_failed", request_id=parsed.request_id, error=str(exc))
        raise _GenerateError(502, "retrieve_failed") from exc
    retrieve_ms = int((time.perf_counter() - retrieve_started) * 1000)

    # GEN-005 AC-3/AC-4: ACL post-retrieve filter — must run BEFORE max_score check.
    acl_result = filter_chunks_by_tier(chunks, parsed.user_tier)
    if not acl_result.visible and acl_result.blocked_count >= 1:
        return await _run_mystery(
            ctx, intent_result, retrieve_ms=retrieve_ms, blocked_count=acl_result.blocked_count
        )

    # Use only visible chunks for downstream processing (AC-5).
    visible_chunks = acl_result.visible
    blocked_count = acl_result.blocked_count

    # AC-2: evaluate max cosine score; if below threshold, divert to Mode 3 lore-gap.
    max_score = max((c.score for c in visible_chunks), default=0.0)
    if max_score < LORE_GAP_THRESHOLD:
        return await _run_lore_gap(
            ctx, intent_result, chunks=visible_chunks, retrieve_ms=retrieve_ms
        )

    messages = build_messages(
        parsed.query,
        visible_chunks,
        suspected_injection=bool(ctx.suspected),
        history=ctx.memory_messages,
    )

    llm_started = time.perf_counter()
    try:
        ai_message = await ctx.llm_client.invoke(messages)
    except LlmTimeoutError:
        latency_ms = int((time.perf_counter() - ctx.started) * 1000)
        await ctx.query_log_repo.insert(
            QueryLogRow(
                request_id=parsed.request_id,
                user_id=parsed.user_id,
                conversation_id=ctx.conversation_id,
                query_text=parsed.query,
                mode="canon",
                intent=intent_result.intent,
                status_code=504,
                latency_ms=latency_ms,
                prompt_tokens=None,
                completion_tokens=None,
                cost_eur=None,
            )
        )
        raise _GenerateError(504, "llm_timeout") from None
    except LlmUpstreamError as exc:
        latency_ms = int((time.perf_counter() - ctx.started) * 1000)
        logger.error("llm_upstream", request_id=parsed.request_id, status=exc.status_code)
        await ctx.query_log_repo.insert(
            QueryLogRow(
                request_id=parsed.request_id,
                user_id=parsed.user_id,
                conversation_id=ctx.conversation_id,
                query_text=parsed.query,
                mode="canon",
                intent=intent_result.intent,
                status_code=502,
                latency_ms=latency_ms,
                prompt_tokens=None,
                completion_tokens=None,
                cost_eur=None,
            )
        )
        raise _GenerateError(502, "llm_upstream") from exc
    llm_ms = int((time.perf_counter() - llm_started) * 1000)

    answer = str(ai_message.content) if ai_message.content is not None else ""
    citations = extract_citations(answer, visible_chunks)
    if not citations and visible_chunks:
        logger.warning("llm_no_citation", request_id=parsed.request_id)

    usage = extract_usage(ai_message, ctx.llm_client.provider)
    cost_eur = compute_cost_eur(ctx.llm_client.model, usage.prompt_tokens, usage.completion_tokens)
    if cost_eur is None and usage.prompt_tokens is not None:
        logger.warning("unknown_model_pricing", model=ctx.llm_client.model)
    canon_usage = Usage(
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        cost_eur=cost_eur,
    )

    now = datetime.now(UTC)
    await ctx.conversation_client.append_message(
        conversation_id=ctx.conversation_id,
        role="user",
        content=parsed.query,
        timestamp=now,
        user_id=parsed.user_id,
    )
    await ctx.conversation_client.append_message(
        conversation_id=ctx.conversation_id,
        role="assistant",
        content=answer,
        timestamp=datetime.now(UTC),
        user_id=parsed.user_id,
    )

    latency_ms = int((time.perf_counter() - ctx.started) * 1000)
    await ctx.query_log_repo.insert(
        QueryLogRow(
            request_id=parsed.request_id,
            user_id=parsed.user_id,
            conversation_id=ctx.conversation_id,
            query_text=parsed.query,
            mode="canon",
            intent=intent_result.intent,
            status_code=200,
            latency_ms=latency_ms,
            prompt_tokens=canon_usage.prompt_tokens,
            completion_tokens=canon_usage.completion_tokens,
            cost_eur=canon_usage.cost_eur,
        )
    )

    # U-5: blocked_count kwarg added only when canon AND blocked_count > 0 (AC-18).
    extra_log: dict[str, Any] = {}
    if blocked_count > 0:
        extra_log["blocked_count"] = blocked_count
    logger.info(
        "generate",
        request_id=parsed.request_id,
        conversation_id=ctx.conversation_id,
        intent=intent_result.intent,
        mode="canon",
        status=200,
        retrieve_ms=retrieve_ms,
        llm_ms=llm_ms,
        chunks=len(visible_chunks),
        citations=len(citations),
        **extra_log,
    )
    return GenerateResponse(
        answer=answer,
        citations=citations,
        mode="canon",
        conversation_id=ctx.conversation_id,
        request_id=parsed.request_id,
        usage=canon_usage,
        retrieve_ms=retrieve_ms,
        llm_ms=llm_ms,
    )


async def _run_mystery(
    ctx: _RequestContext,
    intent_result: IntentResult,
    *,
    retrieve_ms: int,
    blocked_count: int,
) -> GenerateResponse:
    """Mode 4 mystery — all top-K chunks ACL-blocked; evasive LLM response (AC-4, AC-7/8).

    Timing-constant: calls LLM, persists conversation (2 POST ING-003), inserts query_log.
    No chunk content reaches the LLM — only the mystery system prompt + user query (AC-8).
    """
    parsed = ctx.parsed
    mystery_messages = build_mystery_messages(
        parsed.query, suspected_injection=bool(ctx.suspected), history=ctx.memory_messages
    )

    llm_started = time.perf_counter()
    try:
        mystery_ai = await ctx.llm_client.invoke(mystery_messages)
    except LlmTimeoutError:
        latency_ms = int((time.perf_counter() - ctx.started) * 1000)
        await ctx.query_log_repo.insert(
            QueryLogRow(
                request_id=parsed.request_id,
                user_id=parsed.user_id,
                conversation_id=ctx.conversation_id,
                query_text=parsed.query,
                mode="mystery",
                intent=intent_result.intent,
                status_code=504,
                latency_ms=latency_ms,
                prompt_tokens=None,
                completion_tokens=None,
                cost_eur=None,
            )
        )
        raise _GenerateError(504, "llm_timeout") from None
    except LlmUpstreamError as exc:
        latency_ms = int((time.perf_counter() - ctx.started) * 1000)
        logger.error("llm_upstream", request_id=parsed.request_id, status=exc.status_code)
        await ctx.query_log_repo.insert(
            QueryLogRow(
                request_id=parsed.request_id,
                user_id=parsed.user_id,
                conversation_id=ctx.conversation_id,
                query_text=parsed.query,
                mode="mystery",
                intent=intent_result.intent,
                status_code=502,
                latency_ms=latency_ms,
                prompt_tokens=None,
                completion_tokens=None,
                cost_eur=None,
            )
        )
        raise _GenerateError(502, "llm_upstream") from exc
    llm_ms = int((time.perf_counter() - llm_started) * 1000)

    answer = str(mystery_ai.content) if mystery_ai.content is not None else ""

    usage_raw = extract_usage(mystery_ai, ctx.llm_client.provider)
    cost_eur = compute_cost_eur(
        ctx.llm_client.model, usage_raw.prompt_tokens, usage_raw.completion_tokens
    )
    if cost_eur is None and usage_raw.prompt_tokens is not None:
        logger.warning("unknown_model_pricing", model=ctx.llm_client.model)
    mystery_usage = Usage(
        prompt_tokens=usage_raw.prompt_tokens,
        completion_tokens=usage_raw.completion_tokens,
        cost_eur=cost_eur,
    )

    # AC-11: persist conversation (2 sequential posts, same as canon).
    now = datetime.now(UTC)
    await ctx.conversation_client.append_message(
        conversation_id=ctx.conversation_id,
        role="user",
        content=parsed.query,
        timestamp=now,
        user_id=parsed.user_id,
    )
    await ctx.conversation_client.append_message(
        conversation_id=ctx.conversation_id,
        role="assistant",
        content=answer,
        timestamp=datetime.now(UTC),
        user_id=parsed.user_id,
    )

    latency_ms = int((time.perf_counter() - ctx.started) * 1000)
    await ctx.query_log_repo.insert(
        QueryLogRow(
            request_id=parsed.request_id,
            user_id=parsed.user_id,
            conversation_id=ctx.conversation_id,
            query_text=parsed.query,
            mode="mystery",
            intent=intent_result.intent,
            status_code=200,
            latency_ms=latency_ms,
            prompt_tokens=mystery_usage.prompt_tokens,
            completion_tokens=mystery_usage.completion_tokens,
            cost_eur=mystery_usage.cost_eur,
        )
    )

    # AC-18: blocked_count always present for mystery (AC-18).
    logger.info(
        "generate",
        request_id=parsed.request_id,
        conversation_id=ctx.conversation_id,
        intent=intent_result.intent,
        mode="mystery",
        status=200,
        retrieve_ms=retrieve_ms,
        llm_ms=llm_ms,
        chunks=blocked_count,
        citations=0,
        blocked_count=blocked_count,
    )
    return GenerateResponse(
        answer=answer,
        citations=[],
        mode="mystery",
        conversation_id=ctx.conversation_id,
        request_id=parsed.request_id,
        usage=mystery_usage,
        retrieve_ms=retrieve_ms,
        llm_ms=llm_ms,
    )


async def _run_lore_gap(
    ctx: _RequestContext,
    intent_result: IntentResult,
    *,
    chunks: list[Any],
    retrieve_ms: int,
) -> GenerateResponse:
    """Mode 3 lore_gap — noté pour archives, no lore chunks injected (AC-3/AC-5)."""
    parsed = ctx.parsed
    max_score = max((c.score for c in chunks), default=0.0)

    # AC-5: build_lore_gap_messages — no chunks, only the raw query.
    lore_gap_messages = build_lore_gap_messages(
        parsed.query, suspected_injection=bool(ctx.suspected), history=ctx.memory_messages
    )

    llm_started = time.perf_counter()
    try:
        lore_gap_ai = await ctx.llm_client.invoke(lore_gap_messages)
    except LlmTimeoutError:
        latency_ms = int((time.perf_counter() - ctx.started) * 1000)
        # AC-13: only classifier tokens; ticket NOT created.
        await ctx.query_log_repo.insert(
            QueryLogRow(
                request_id=parsed.request_id,
                user_id=parsed.user_id,
                conversation_id=ctx.conversation_id,
                query_text=parsed.query,
                mode="lore_gap",
                intent=intent_result.intent,
                status_code=504,
                latency_ms=latency_ms,
                prompt_tokens=intent_result.prompt_tokens,
                completion_tokens=intent_result.completion_tokens,
                cost_eur=intent_result.cost_eur,
            )
        )
        raise _GenerateError(504, "llm_timeout") from None
    except LlmUpstreamError as exc:
        latency_ms = int((time.perf_counter() - ctx.started) * 1000)
        logger.error("llm_upstream", request_id=parsed.request_id, status=exc.status_code)
        # AC-14: only classifier tokens; ticket NOT created.
        await ctx.query_log_repo.insert(
            QueryLogRow(
                request_id=parsed.request_id,
                user_id=parsed.user_id,
                conversation_id=ctx.conversation_id,
                query_text=parsed.query,
                mode="lore_gap",
                intent=intent_result.intent,
                status_code=502,
                latency_ms=latency_ms,
                prompt_tokens=intent_result.prompt_tokens,
                completion_tokens=intent_result.completion_tokens,
                cost_eur=intent_result.cost_eur,
            )
        )
        raise _GenerateError(502, "llm_upstream") from exc
    llm_ms = intent_result.latency_ms + int((time.perf_counter() - llm_started) * 1000)

    answer = str(lore_gap_ai.content) if lore_gap_ai.content is not None else ""

    # AC-7: aggregate usage from classifier + lore_gap LLM.
    lore_raw_usage = extract_usage(lore_gap_ai, ctx.llm_client.provider)
    lore_cost = compute_cost_eur(
        ctx.llm_client.model,
        lore_raw_usage.prompt_tokens,
        lore_raw_usage.completion_tokens,
    )
    combined_prompt = _add_optional(intent_result.prompt_tokens, lore_raw_usage.prompt_tokens)
    combined_completion = _add_optional(
        intent_result.completion_tokens, lore_raw_usage.completion_tokens
    )
    combined_cost = _add_decimal_optional(intent_result.cost_eur, lore_cost)
    if combined_cost is None and combined_prompt is not None:
        logger.warning("unknown_model_pricing", model=ctx.llm_client.model)

    lore_gap_usage = Usage(
        prompt_tokens=combined_prompt,
        completion_tokens=combined_completion,
        cost_eur=combined_cost,
    )

    # AC-8: persist conversation (2 sequential posts: user then assistant).
    now = datetime.now(UTC)
    user_result = await ctx.conversation_client.append_message(
        conversation_id=ctx.conversation_id,
        role="user",
        content=parsed.query,
        timestamp=now,
        user_id=parsed.user_id,
    )
    asst_result = await ctx.conversation_client.append_message(
        conversation_id=ctx.conversation_id,
        role="assistant",
        content=answer,
        timestamp=datetime.now(UTC),
        user_id=parsed.user_id,
    )
    conversation_logged = user_result.ok and asst_result.ok

    # AC-9: ticket_service AFTER ING-003. If ING-003 failed, skip ticket (FK safety, D4).
    ticket_action = "skipped_error"
    if conversation_logged and ctx.db_pool is not None and ctx.embedder is not None:
        ticket_result: TicketResult = await create_or_increment(
            ctx.db_pool,
            ctx.embedder,
            conversation_id=ctx.conversation_id,
            # AC-12: store raw query (no prefix) in tickets.question.
            question=parsed.query,
            request_id=parsed.request_id,
        )
        ticket_action = ticket_result.action
    elif not conversation_logged:
        logger.warning(
            "ticket_service_skipped_after_conversation_log_failed",
            request_id=parsed.request_id,
        )

    # AC-11: INSERT query_log.
    latency_ms = int((time.perf_counter() - ctx.started) * 1000)
    await ctx.query_log_repo.insert(
        QueryLogRow(
            request_id=parsed.request_id,
            user_id=parsed.user_id,
            conversation_id=ctx.conversation_id,
            query_text=parsed.query,
            mode="lore_gap",
            intent=intent_result.intent,
            status_code=200,
            latency_ms=latency_ms,
            prompt_tokens=lore_gap_usage.prompt_tokens,
            completion_tokens=lore_gap_usage.completion_tokens,
            cost_eur=lore_gap_usage.cost_eur,
        )
    )

    # AC-18: log INFO with top_score + ticket_action + chunks + citations=0.
    logger.info(
        "generate",
        request_id=parsed.request_id,
        conversation_id=ctx.conversation_id,
        intent=intent_result.intent,
        mode="lore_gap",
        status=200,
        retrieve_ms=retrieve_ms,
        llm_ms=llm_ms,
        chunks=len(chunks),
        citations=0,
        top_score=round(max_score, 4),
        ticket_action=ticket_action,
    )
    return GenerateResponse(
        answer=answer,
        citations=[],
        mode="lore_gap",
        conversation_id=ctx.conversation_id,
        request_id=parsed.request_id,
        usage=lore_gap_usage,
        retrieve_ms=retrieve_ms,
        llm_ms=llm_ms,
    )


async def _run_off_topic(ctx: _RequestContext, intent_result: IntentResult) -> GenerateResponse:
    """Mode 2 off_topic — LLM refusal, no retrieve, no canon LLM (AC-3/AC-7)."""
    parsed = ctx.parsed

    refusal_messages = build_off_topic_messages(
        parsed.query, suspected_injection=bool(ctx.suspected), history=ctx.memory_messages
    )
    refusal_started = time.perf_counter()
    try:
        refusal_ai = await ctx.llm_client.invoke(refusal_messages)
    except LlmTimeoutError:
        latency_ms = int((time.perf_counter() - ctx.started) * 1000)
        # AC-15: partial query_log with classifier usage only.
        await ctx.query_log_repo.insert(
            QueryLogRow(
                request_id=parsed.request_id,
                user_id=parsed.user_id,
                conversation_id=ctx.conversation_id,
                query_text=parsed.query,
                mode="off_topic",
                intent="off_topic",
                status_code=504,
                latency_ms=latency_ms,
                prompt_tokens=intent_result.prompt_tokens,
                completion_tokens=intent_result.completion_tokens,
                cost_eur=intent_result.cost_eur,
            )
        )
        raise _GenerateError(504, "llm_timeout") from None
    except LlmUpstreamError as exc:
        latency_ms = int((time.perf_counter() - ctx.started) * 1000)
        logger.error("llm_upstream", request_id=parsed.request_id, status=exc.status_code)
        # AC-16: partial query_log with classifier usage only.
        await ctx.query_log_repo.insert(
            QueryLogRow(
                request_id=parsed.request_id,
                user_id=parsed.user_id,
                conversation_id=ctx.conversation_id,
                query_text=parsed.query,
                mode="off_topic",
                intent="off_topic",
                status_code=502,
                latency_ms=latency_ms,
                prompt_tokens=intent_result.prompt_tokens,
                completion_tokens=intent_result.completion_tokens,
                cost_eur=intent_result.cost_eur,
            )
        )
        raise _GenerateError(502, "llm_upstream") from exc
    refusal_ms = int((time.perf_counter() - refusal_started) * 1000)

    answer = str(refusal_ai.content) if refusal_ai.content is not None else ""

    # AC-10: aggregate usage from classifier + refusal LLM.
    refusal_raw_usage = extract_usage(refusal_ai, ctx.llm_client.provider)
    refusal_cost = compute_cost_eur(
        ctx.llm_client.model,
        refusal_raw_usage.prompt_tokens,
        refusal_raw_usage.completion_tokens,
    )

    combined_prompt = _add_optional(intent_result.prompt_tokens, refusal_raw_usage.prompt_tokens)
    combined_completion = _add_optional(
        intent_result.completion_tokens, refusal_raw_usage.completion_tokens
    )
    combined_cost = _add_decimal_optional(intent_result.cost_eur, refusal_cost)

    if combined_cost is None and combined_prompt is not None:
        logger.warning("unknown_model_pricing", model=ctx.llm_client.model)

    off_topic_usage = Usage(
        prompt_tokens=combined_prompt,
        completion_tokens=combined_completion,
        cost_eur=combined_cost,
    )

    # AC-9: retrieve_ms=0, llm_ms = classifier + refusal.
    llm_ms = intent_result.latency_ms + refusal_ms

    # AC-11: persist conversation messages.
    now = datetime.now(UTC)
    await ctx.conversation_client.append_message(
        conversation_id=ctx.conversation_id,
        role="user",
        content=parsed.query,
        timestamp=now,
        user_id=parsed.user_id,
    )
    await ctx.conversation_client.append_message(
        conversation_id=ctx.conversation_id,
        role="assistant",
        content=answer,
        timestamp=datetime.now(UTC),
        user_id=parsed.user_id,
    )

    # AC-12: insert query_log.
    latency_ms = int((time.perf_counter() - ctx.started) * 1000)
    await ctx.query_log_repo.insert(
        QueryLogRow(
            request_id=parsed.request_id,
            user_id=parsed.user_id,
            conversation_id=ctx.conversation_id,
            query_text=parsed.query,
            mode="off_topic",
            intent="off_topic",
            status_code=200,
            latency_ms=latency_ms,
            prompt_tokens=off_topic_usage.prompt_tokens,
            completion_tokens=off_topic_usage.completion_tokens,
            cost_eur=off_topic_usage.cost_eur,
        )
    )

    # AC-20: log INFO with intent, chunks=0, citations=0.
    logger.info(
        "generate",
        request_id=parsed.request_id,
        conversation_id=ctx.conversation_id,
        intent="off_topic",
        mode="off_topic",
        status=200,
        retrieve_ms=0,
        llm_ms=llm_ms,
        chunks=0,
        citations=0,
    )
    return GenerateResponse(
        answer=answer,
        citations=[],
        mode="off_topic",
        conversation_id=ctx.conversation_id,
        request_id=parsed.request_id,
        usage=off_topic_usage,
        retrieve_ms=0,
        llm_ms=llm_ms,
    )


def _add_optional(a: int | None, b: int | None) -> int | None:
    if a is None or b is None:
        return None
    return a + b


def _add_decimal_optional(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    if a is None or b is None:
        return None
    return a + b
