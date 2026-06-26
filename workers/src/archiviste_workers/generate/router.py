"""POST /v1/generate — Modes 1-4 (canon, off_topic, lore_gap, mystery). Internal-only."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from langchain_core.messages import BaseMessage
from pydantic import ValidationError

from archiviste_workers.generate._pipeline import (
    GenerationResult,
    PreparedMode,
    add_decimal_optional,
    add_optional,
    finalize_generation,
)
from archiviste_workers.generate.injection_filter import detect_injection
from archiviste_workers.generate.memory import load_memory_window
from archiviste_workers.generate.models import (
    LORE_GAP_THRESHOLD,
    GenerateRequest,
    GenerateResponse,
    Usage,
    is_valid_uuid,
)
from archiviste_workers.generate.parser import extract_citations, extract_followups
from archiviste_workers.generate.pricing import compute_cost_eur
from archiviste_workers.generate.prompt import (
    build_lore_gap_messages,
    build_messages,
    build_mystery_messages,
    build_off_topic_messages,
)
from archiviste_workers.services.acl import filter_chunks_by_tier
from archiviste_workers.services.conversation_client import ConversationClient
from archiviste_workers.services.intent import classify_intent
from archiviste_workers.services.llm import (
    LlmClient,
    LlmTimeoutError,
    LlmUpstreamError,
    extract_usage,
)
from archiviste_workers.services.query_log import QueryLogRepository, QueryLogRow
from archiviste_workers.services.retrieve_client import RetrieveClient, RetrieveError

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

    prepared = await _decide_mode(ctx, intent_result)
    return await _invoke_and_finalize(ctx, prepared)


async def _decide_mode(ctx: _RequestContext, intent_result: Any) -> PreparedMode:
    """Retrieve → ACL → score check → build mode messages. Returns PreparedMode.

    Message-building functions are called from this module's namespace so that
    tests can patch them via patch.object(router_module, "build_messages", …).
    """
    parsed = ctx.parsed

    if intent_result.intent == "off_topic":
        messages = build_off_topic_messages(
            parsed.query,
            suspected_injection=bool(ctx.suspected),
            history=ctx.memory_messages,
        )
        return PreparedMode(
            mode="off_topic",
            messages=messages,
            visible_chunks=[],
            retrieve_ms=0,
            max_score=0.0,
            blocked_count=0,
            intent_result=intent_result,
        )

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

    # GEN-005 AC-3/AC-4: ACL post-retrieve filter before max_score check.
    acl_result = filter_chunks_by_tier(chunks, parsed.user_tier)

    if not acl_result.visible and acl_result.blocked_count >= 1:
        mystery_messages = build_mystery_messages(
            parsed.query, suspected_injection=bool(ctx.suspected), history=ctx.memory_messages
        )
        return PreparedMode(
            mode="mystery",
            messages=mystery_messages,
            visible_chunks=[],
            retrieve_ms=retrieve_ms,
            max_score=0.0,
            blocked_count=acl_result.blocked_count,
            intent_result=intent_result,
        )

    visible_chunks = acl_result.visible
    max_score = max((c.score for c in visible_chunks), default=0.0)

    if max_score < LORE_GAP_THRESHOLD:
        lore_gap_messages = build_lore_gap_messages(
            parsed.query, suspected_injection=bool(ctx.suspected), history=ctx.memory_messages
        )
        return PreparedMode(
            mode="lore_gap",
            messages=lore_gap_messages,
            visible_chunks=visible_chunks,
            retrieve_ms=retrieve_ms,
            max_score=max_score,
            blocked_count=acl_result.blocked_count,
            intent_result=intent_result,
        )

    canon_messages = build_messages(
        parsed.query,
        visible_chunks,
        suspected_injection=bool(ctx.suspected),
        history=ctx.memory_messages,
    )
    return PreparedMode(
        mode="canon",
        messages=canon_messages,
        visible_chunks=visible_chunks,
        retrieve_ms=retrieve_ms,
        max_score=max_score,
        blocked_count=acl_result.blocked_count,
        intent_result=intent_result,
    )


async def _invoke_and_finalize(
    ctx: _RequestContext,
    prepared: PreparedMode,
) -> GenerateResponse:
    """Invoke the LLM, handle failure-path query_log inserts, then finalize."""
    parsed = ctx.parsed
    intent_result = prepared.intent_result

    llm_started = time.perf_counter()
    try:
        ai_message = await ctx.llm_client.invoke(prepared.messages)
    except LlmTimeoutError:
        await ctx.query_log_repo.insert(_failure_row(ctx, prepared, 504))
        raise _GenerateError(504, "llm_timeout") from None
    except LlmUpstreamError as exc:
        logger.error("llm_upstream", request_id=parsed.request_id, status=exc.status_code)
        await ctx.query_log_repo.insert(_failure_row(ctx, prepared, 502))
        raise _GenerateError(502, "llm_upstream") from exc

    llm_ms_raw = int((time.perf_counter() - llm_started) * 1000)
    # lore_gap/off_topic llm_ms includes classifier latency (AC-7/AC-9).
    llm_ms = (
        intent_result.latency_ms + llm_ms_raw
        if prepared.mode in ("lore_gap", "off_topic")
        else llm_ms_raw
    )

    raw_answer = str(ai_message.content) if ai_message.content is not None else ""
    # #354: strip the sentinel follow-up block from the emitted body; no marker on canon.
    answer, followups = extract_followups(raw_answer)
    citations = extract_citations(answer, prepared.visible_chunks)
    if not citations and prepared.visible_chunks:
        logger.warning("llm_no_citation", request_id=parsed.request_id)

    usage = _build_usage(ctx, prepared, ai_message)
    result = GenerationResult(
        answer=answer, usage=usage, citations=citations, llm_ms=llm_ms, followups=followups
    )

    await finalize_generation(ctx, prepared, result, log_event="generate")

    return GenerateResponse(
        answer=answer,
        citations=citations,
        mode=prepared.mode,
        conversation_id=ctx.conversation_id,
        request_id=parsed.request_id,
        usage=usage,
        retrieve_ms=prepared.retrieve_ms,
        llm_ms=llm_ms,
    )


def _failure_row(ctx: _RequestContext, prepared: PreparedMode, status_code: int) -> QueryLogRow:
    """Build a failure-path query_log row (no LLM tokens; classifier tokens for lore_gap/off_topic).

    AC-13/AC-14 (lore_gap): only classifier tokens on LLM timeout/upstream.
    AC-15/AC-16 (off_topic): only classifier tokens on LLM timeout/upstream.
    canon/mystery: None tokens (no LLM call completed).
    """
    parsed = ctx.parsed
    intent_result = prepared.intent_result
    latency_ms = int((time.perf_counter() - ctx.started) * 1000)
    intent_val = "off_topic" if prepared.mode == "off_topic" else intent_result.intent
    use_classifier_tokens = prepared.mode in ("lore_gap", "off_topic")
    return QueryLogRow(
        request_id=parsed.request_id,
        user_id=parsed.user_id,
        conversation_id=ctx.conversation_id,
        query_text=parsed.query,
        mode=prepared.mode,
        intent=intent_val,
        status_code=status_code,
        latency_ms=latency_ms,
        prompt_tokens=intent_result.prompt_tokens if use_classifier_tokens else None,
        completion_tokens=intent_result.completion_tokens if use_classifier_tokens else None,
        cost_eur=intent_result.cost_eur if use_classifier_tokens else None,
    )


def _build_usage(ctx: _RequestContext, prepared: PreparedMode, ai_message: Any) -> Usage:
    """Compute Usage, combining classifier tokens for lore_gap and off_topic (AC-7/AC-10)."""
    raw = extract_usage(ai_message, ctx.llm_client.provider)
    cost = compute_cost_eur(ctx.llm_client.model, raw.prompt_tokens, raw.completion_tokens)
    if cost is None and raw.prompt_tokens is not None:
        logger.warning("unknown_model_pricing", model=ctx.llm_client.model)

    if prepared.mode in ("lore_gap", "off_topic"):
        ir = prepared.intent_result
        return Usage(
            prompt_tokens=add_optional(ir.prompt_tokens, raw.prompt_tokens),
            completion_tokens=add_optional(ir.completion_tokens, raw.completion_tokens),
            cost_eur=add_decimal_optional(ir.cost_eur, cost),
        )
    return Usage(
        prompt_tokens=raw.prompt_tokens,
        completion_tokens=raw.completion_tokens,
        cost_eur=cost,
    )
