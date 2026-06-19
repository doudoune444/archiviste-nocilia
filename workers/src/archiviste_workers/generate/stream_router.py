"""POST /v1/generate/stream — SSE token streaming, same pipeline as /v1/generate (CHAT-001).

AC references:
- CHAT-001 AC-1: SSE event grammar meta -> token* -> (done | error).
- CHAT-001 AC-2: meta emitted first, before any token.
- CHAT-001 AC-3: persist conversation + raise lore-gap ticket ONLY after clean done.
- CHAT-001 AC-4: on mid-stream LLM failure, emit error event, persist nothing, raise no ticket.
- CHAT-001 AC-5: validation failures before streaming return non-200 JSON (not SSE error).
- Security: A09 — never log raw query/answer/tokens; same ACL/injection/memory as blocking path.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.messages import BaseMessage

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
    Citation,
    GenerateRequest,
    Usage,
)
from archiviste_workers.generate.parser import extract_citations
from archiviste_workers.generate.pricing import compute_cost_eur
from archiviste_workers.generate.prompt import (
    build_lore_gap_messages,
    build_messages,
    build_mystery_messages,
    build_off_topic_messages,
)
from archiviste_workers.generate.router import (
    _error_response,
    _GenerateError,
    _parse_request,
)
from archiviste_workers.services.acl import filter_chunks_by_tier
from archiviste_workers.services.intent import IntentResult, classify_intent
from archiviste_workers.services.llm import LlmTimeoutError, LlmUpstreamError, extract_usage
from archiviste_workers.services.query_log import QueryLogRow
from archiviste_workers.services.retrieve_client import RetrieveError
from archiviste_workers.services.ticket_service import create_or_increment

stream_router = APIRouter(prefix="/v1", tags=["generate"])
logger = structlog.get_logger()

TOP_K_STREAM = 5

StreamErrorCode = Literal["llm_timeout", "llm_upstream", "retrieve_failed", "internal"]


# ---------------------------------------------------------------------------
# SSE framing helpers
# ---------------------------------------------------------------------------


def _decimal_to_float(obj: Any) -> Any:
    """JSON default encoder: serialize Decimal as float."""
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _sse_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=_decimal_to_float)}\n\n"


def _sse_meta(mode: str, conversation_id: str, request_id: str) -> str:
    return _sse_event(
        "meta",
        {"mode": mode, "conversation_id": conversation_id, "request_id": request_id},
    )


def _sse_token(text: str) -> str:
    return _sse_event("token", {"text": text})


def _sse_done(
    citations: list[Citation],
    usage: Usage,
    retrieve_ms: int,
    llm_ms: int,
) -> str:
    return _sse_event(
        "done",
        {
            "citations": [c.model_dump() for c in citations],
            "usage": usage.model_dump(),
            "retrieve_ms": retrieve_ms,
            "llm_ms": llm_ms,
        },
    )


def _sse_error(code: StreamErrorCode) -> str:
    return _sse_event("error", {"error": code})


# ---------------------------------------------------------------------------
# Context dataclass — carries service handles to keep _stream_* / finalize_generation
# signatures within the 4-parameter clean-code limit.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _StreamContext:
    parsed: GenerateRequest
    conversation_id: str
    suspected: str | None
    request: Request
    started: float
    memory_messages: list[BaseMessage]
    retrieval_query: str
    # Service handles — resolved once at request entry and threaded via context
    # so finalize_generation does not need the Request object directly.
    conversation_client: Any
    query_log_repo: Any
    db_pool: Any
    embedder: Any


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@stream_router.post("/generate/stream", response_model=None)
async def post_generate_stream(
    request: Request, payload: dict[str, Any]
) -> StreamingResponse | JSONResponse:
    """SSE streaming endpoint — CHAT-001 AC-5: pre-stream errors return JSON."""
    try:
        parsed = _parse_request(payload, request.headers)
    except _GenerateError as exc:
        return _error_response(exc.status, exc.code)

    conversation_id = parsed.conversation_id or str(uuid.uuid4())
    suspected = detect_injection(parsed.query)
    if suspected:
        logger.warning(
            "prompt_injection_suspected", request_id=parsed.request_id, pattern=suspected
        )

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
    history_suspected = window.last_user_turn is not None and bool(
        detect_injection(window.last_user_turn)
    )

    ctx = _StreamContext(
        parsed=parsed,
        conversation_id=conversation_id,
        suspected=suspected,
        request=request,
        started=time.perf_counter(),
        memory_messages=window.messages,
        retrieval_query=retrieval_query,
        conversation_client=request.app.state.conversation_client,
        query_log_repo=request.app.state.query_log_repo,
        db_pool=getattr(request.app.state, "db_pool", None),
        embedder=getattr(request.app.state, "embedder", None),
    )

    intent_result = await classify_intent(
        llm_client=request.app.state.llm_client,
        query=ctx.retrieval_query,
        suspected_injection=bool(suspected) or history_suspected,
        request_id=parsed.request_id,
    )

    return StreamingResponse(
        _stream_generate(ctx, intent_result),
        media_type="text/event-stream",
        headers={"X-Content-Type-Options": "nosniff", "x-request-id": parsed.request_id},
    )


# ---------------------------------------------------------------------------
# Generator dispatcher
# ---------------------------------------------------------------------------


async def _stream_generate(
    ctx: _StreamContext,
    intent_result: IntentResult,
) -> AsyncIterator[str]:
    if intent_result.intent == "off_topic":
        async for chunk in _stream_off_topic(ctx, intent_result):
            yield chunk
    else:
        async for chunk in _stream_canon(ctx, intent_result):
            yield chunk


# ---------------------------------------------------------------------------
# LLM streaming helpers
# ---------------------------------------------------------------------------


async def _collect_llm_stream(
    llm_client: Any,
    messages: list[Any],
) -> tuple[list[str], Any]:
    """Drive the LLM astream; collect token strings and the final aggregated message.

    Raises LlmTimeoutError or LlmUpstreamError on failure (same contract as invoke()).
    Returns (token_parts, final_message).
    """
    token_parts: list[str] = []
    final_message = None
    async for delta, maybe_final in llm_client.astream(messages):
        if maybe_final is not None:
            final_message = maybe_final
        else:
            token_parts.append(delta)
    return token_parts, final_message


def _build_stream_usage(llm_client: Any, final_message: Any) -> Usage:
    """Extract and compute cost from the aggregated LLM message."""
    raw = (
        extract_usage(final_message, llm_client.provider)
        if final_message
        else Usage(prompt_tokens=None, completion_tokens=None)
    )
    cost = compute_cost_eur(llm_client.model, raw.prompt_tokens, raw.completion_tokens)
    if cost is None and raw.prompt_tokens is not None:
        logger.warning("unknown_model_pricing", model=llm_client.model)
    return Usage(
        prompt_tokens=raw.prompt_tokens,
        completion_tokens=raw.completion_tokens,
        cost_eur=cost,
    )


def _combine_usage(intent_result: IntentResult, raw_usage: Usage, llm_client: Any) -> Usage:
    """Aggregate classifier + mode LLM usage for off_topic and lore_gap (AC-7/AC-10)."""
    raw_cost = compute_cost_eur(
        llm_client.model, raw_usage.prompt_tokens, raw_usage.completion_tokens
    )
    combined_prompt = add_optional(intent_result.prompt_tokens, raw_usage.prompt_tokens)
    combined_completion = add_optional(intent_result.completion_tokens, raw_usage.completion_tokens)
    combined_cost = add_decimal_optional(intent_result.cost_eur, raw_cost)
    if combined_cost is None and combined_prompt is not None:
        logger.warning("unknown_model_pricing", model=llm_client.model)
    return Usage(
        prompt_tokens=combined_prompt,
        completion_tokens=combined_completion,
        cost_eur=combined_cost,
    )


async def _insert_failure_log(
    ctx: _StreamContext,
    intent_result: IntentResult,
    *,
    mode: str,
    status_code: int,
    prompt_tokens: int | None,
    completion_tokens: int | None,
) -> None:
    """Insert a query_log row on every failure path — parity with blocking router.py (FIX 1)."""
    latency_ms = int((time.perf_counter() - ctx.started) * 1000)
    intent_val = "off_topic" if mode == "off_topic" else intent_result.intent
    await ctx.query_log_repo.insert(
        QueryLogRow(
            request_id=ctx.parsed.request_id,
            user_id=ctx.parsed.user_id,
            conversation_id=ctx.conversation_id,
            query_text=ctx.parsed.query,
            mode=mode,
            intent=intent_val,
            status_code=status_code,
            latency_ms=latency_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_eur=None,
        )
    )


# ---------------------------------------------------------------------------
# Mode branches
# ---------------------------------------------------------------------------


async def _stream_canon(
    ctx: _StreamContext,
    intent_result: IntentResult,
) -> AsyncIterator[str]:
    """Canon/lore_gap/mystery streaming pipeline — retrieve then branch."""
    parsed = ctx.parsed
    llm_client = ctx.request.app.state.llm_client
    retrieve_client = ctx.request.app.state.retrieve_client

    retrieve_started = time.perf_counter()
    try:
        chunks = await retrieve_client.search(
            query=ctx.retrieval_query,
            top_k=TOP_K_STREAM,
            user_tier=parsed.user_tier,
            request_id=parsed.request_id,
        )
    except RetrieveError as exc:
        logger.error("retrieve_failed", request_id=parsed.request_id, error=str(exc))
        # FIX 1: insert query_log on retrieve failure (parity with blocking router, status=502).
        await _insert_failure_log(
            ctx,
            intent_result,
            mode="canon",
            status_code=502,
            prompt_tokens=None,
            completion_tokens=None,
        )
        yield _sse_error("retrieve_failed")
        return
    retrieve_ms = int((time.perf_counter() - retrieve_started) * 1000)

    acl_result = filter_chunks_by_tier(chunks, parsed.user_tier)

    if not acl_result.visible and acl_result.blocked_count >= 1:
        async for event in _stream_mystery(
            ctx, intent_result, retrieve_ms=retrieve_ms, blocked_count=acl_result.blocked_count
        ):
            yield event
        return

    visible_chunks = acl_result.visible
    blocked_count = acl_result.blocked_count
    max_score = max((c.score for c in visible_chunks), default=0.0)

    if max_score < LORE_GAP_THRESHOLD:
        async for event in _stream_lore_gap(
            ctx, intent_result, chunks=visible_chunks, retrieve_ms=retrieve_ms
        ):
            yield event
        return

    messages = build_messages(
        parsed.query,
        visible_chunks,
        suspected_injection=bool(ctx.suspected),
        history=ctx.memory_messages,
    )

    yield _sse_meta("canon", ctx.conversation_id, parsed.request_id)

    llm_started = time.perf_counter()
    try:
        token_parts, final_message = await _collect_llm_stream(llm_client, messages)
    except LlmTimeoutError:
        # FIX 1: insert query_log on LLM timeout (parity, status=504, tokens=None).
        await _insert_failure_log(
            ctx,
            intent_result,
            mode="canon",
            status_code=504,
            prompt_tokens=None,
            completion_tokens=None,
        )
        yield _sse_error("llm_timeout")
        return
    except LlmUpstreamError as exc:
        logger.error("llm_upstream", request_id=parsed.request_id, status=exc.status_code)
        # FIX 1: insert query_log on LLM upstream error (parity, status=502, tokens=None).
        await _insert_failure_log(
            ctx,
            intent_result,
            mode="canon",
            status_code=502,
            prompt_tokens=None,
            completion_tokens=None,
        )
        yield _sse_error("llm_upstream")
        return

    for part in token_parts:
        yield _sse_token(part)

    llm_ms = int((time.perf_counter() - llm_started) * 1000)
    answer = "".join(token_parts)
    citations = extract_citations(answer, visible_chunks)
    if not citations and visible_chunks:
        logger.warning("llm_no_citation", request_id=parsed.request_id)

    usage = _build_stream_usage(llm_client, final_message)
    prepared = PreparedMode(
        mode="canon",
        messages=messages,
        visible_chunks=visible_chunks,
        retrieve_ms=retrieve_ms,
        max_score=max_score,
        blocked_count=blocked_count,
        intent_result=intent_result,
    )
    result = GenerationResult(answer=answer, usage=usage, citations=citations, llm_ms=llm_ms)
    await finalize_generation(
        ctx, prepared, result, log_event="generate_stream", ticket_creator=create_or_increment
    )
    yield _sse_done(citations, usage, retrieve_ms, llm_ms)


async def _stream_mystery(
    ctx: _StreamContext,
    intent_result: IntentResult,
    *,
    retrieve_ms: int,
    blocked_count: int,
) -> AsyncIterator[str]:
    llm_client = ctx.request.app.state.llm_client
    mystery_messages = build_mystery_messages(
        ctx.parsed.query, suspected_injection=bool(ctx.suspected), history=ctx.memory_messages
    )

    yield _sse_meta("mystery", ctx.conversation_id, ctx.parsed.request_id)

    llm_started = time.perf_counter()
    try:
        token_parts, final_message = await _collect_llm_stream(llm_client, mystery_messages)
    except LlmTimeoutError:
        # FIX 1: insert query_log on mystery LLM timeout (parity, status=504, tokens=None).
        await _insert_failure_log(
            ctx,
            intent_result,
            mode="mystery",
            status_code=504,
            prompt_tokens=None,
            completion_tokens=None,
        )
        yield _sse_error("llm_timeout")
        return
    except LlmUpstreamError as exc:
        logger.error("llm_upstream", request_id=ctx.parsed.request_id, status=exc.status_code)
        # FIX 1: insert query_log on mystery LLM upstream (parity, status=502, tokens=None).
        await _insert_failure_log(
            ctx,
            intent_result,
            mode="mystery",
            status_code=502,
            prompt_tokens=None,
            completion_tokens=None,
        )
        yield _sse_error("llm_upstream")
        return

    for part in token_parts:
        yield _sse_token(part)

    llm_ms = int((time.perf_counter() - llm_started) * 1000)
    answer = "".join(token_parts)
    usage = _build_stream_usage(llm_client, final_message)
    prepared = PreparedMode(
        mode="mystery",
        messages=mystery_messages,
        visible_chunks=[],
        retrieve_ms=retrieve_ms,
        max_score=0.0,
        blocked_count=blocked_count,
        intent_result=intent_result,
    )
    result = GenerationResult(answer=answer, usage=usage, citations=[], llm_ms=llm_ms)
    await finalize_generation(
        ctx, prepared, result, log_event="generate_stream", ticket_creator=create_or_increment
    )
    yield _sse_done([], usage, retrieve_ms, llm_ms)


async def _stream_lore_gap(
    ctx: _StreamContext,
    intent_result: IntentResult,
    *,
    chunks: list[Any],
    retrieve_ms: int,
) -> AsyncIterator[str]:
    llm_client = ctx.request.app.state.llm_client
    max_score = max((c.score for c in chunks), default=0.0)
    lore_gap_messages = build_lore_gap_messages(
        ctx.parsed.query, suspected_injection=bool(ctx.suspected), history=ctx.memory_messages
    )

    yield _sse_meta("lore_gap", ctx.conversation_id, ctx.parsed.request_id)

    llm_started = time.perf_counter()
    try:
        token_parts, final_message = await _collect_llm_stream(llm_client, lore_gap_messages)
    except LlmTimeoutError:
        # FIX 1: insert query_log on lore_gap LLM timeout.
        # AC-13 parity: only classifier tokens on timeout.
        await _insert_failure_log(
            ctx,
            intent_result,
            mode="lore_gap",
            status_code=504,
            prompt_tokens=intent_result.prompt_tokens,
            completion_tokens=intent_result.completion_tokens,
        )
        yield _sse_error("llm_timeout")
        return
    except LlmUpstreamError as exc:
        logger.error("llm_upstream", request_id=ctx.parsed.request_id, status=exc.status_code)
        # FIX 1: insert query_log on lore_gap LLM upstream.
        # AC-14 parity: only classifier tokens on upstream error.
        await _insert_failure_log(
            ctx,
            intent_result,
            mode="lore_gap",
            status_code=502,
            prompt_tokens=intent_result.prompt_tokens,
            completion_tokens=intent_result.completion_tokens,
        )
        yield _sse_error("llm_upstream")
        return

    for part in token_parts:
        yield _sse_token(part)

    llm_ms = intent_result.latency_ms + int((time.perf_counter() - llm_started) * 1000)
    answer = "".join(token_parts)
    raw = (
        extract_usage(final_message, llm_client.provider)
        if final_message
        else Usage(prompt_tokens=None, completion_tokens=None)
    )
    usage = _combine_usage(intent_result, raw, llm_client)

    prepared = PreparedMode(
        mode="lore_gap",
        messages=lore_gap_messages,
        visible_chunks=chunks,
        retrieve_ms=retrieve_ms,
        max_score=max_score,
        blocked_count=0,
        intent_result=intent_result,
    )
    result = GenerationResult(answer=answer, usage=usage, citations=[], llm_ms=llm_ms)
    await finalize_generation(
        ctx, prepared, result, log_event="generate_stream", ticket_creator=create_or_increment
    )
    yield _sse_done([], usage, retrieve_ms, llm_ms)


async def _stream_off_topic(
    ctx: _StreamContext,
    intent_result: IntentResult,
) -> AsyncIterator[str]:
    llm_client = ctx.request.app.state.llm_client
    refusal_messages = build_off_topic_messages(
        ctx.parsed.query, suspected_injection=bool(ctx.suspected), history=ctx.memory_messages
    )

    yield _sse_meta("off_topic", ctx.conversation_id, ctx.parsed.request_id)

    refusal_started = time.perf_counter()
    try:
        token_parts, final_message = await _collect_llm_stream(llm_client, refusal_messages)
    except LlmTimeoutError:
        # FIX 1: insert query_log on off_topic LLM timeout.
        # AC-15 parity: only classifier tokens on timeout.
        await _insert_failure_log(
            ctx,
            intent_result,
            mode="off_topic",
            status_code=504,
            prompt_tokens=intent_result.prompt_tokens,
            completion_tokens=intent_result.completion_tokens,
        )
        yield _sse_error("llm_timeout")
        return
    except LlmUpstreamError as exc:
        logger.error("llm_upstream", request_id=ctx.parsed.request_id, status=exc.status_code)
        # FIX 1: insert query_log on off_topic LLM upstream.
        # AC-16 parity: only classifier tokens on upstream error.
        await _insert_failure_log(
            ctx,
            intent_result,
            mode="off_topic",
            status_code=502,
            prompt_tokens=intent_result.prompt_tokens,
            completion_tokens=intent_result.completion_tokens,
        )
        yield _sse_error("llm_upstream")
        return

    for part in token_parts:
        yield _sse_token(part)

    refusal_ms = int((time.perf_counter() - refusal_started) * 1000)
    answer = "".join(token_parts)
    raw = (
        extract_usage(final_message, llm_client.provider)
        if final_message
        else Usage(prompt_tokens=None, completion_tokens=None)
    )
    usage = _combine_usage(intent_result, raw, llm_client)
    llm_ms = intent_result.latency_ms + refusal_ms

    prepared = PreparedMode(
        mode="off_topic",
        messages=refusal_messages,
        visible_chunks=[],
        retrieve_ms=0,
        max_score=0.0,
        blocked_count=0,
        intent_result=intent_result,
    )
    result = GenerationResult(answer=answer, usage=usage, citations=[], llm_ms=llm_ms)
    await finalize_generation(
        ctx, prepared, result, log_event="generate_stream", ticket_creator=create_or_increment
    )
    yield _sse_done([], usage, 0, llm_ms)
